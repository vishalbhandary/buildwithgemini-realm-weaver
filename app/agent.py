# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import random
import re
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import (
    AgentEngineSandboxCodeExecutor,
)
from google.adk.memory.vertex_ai_memory_bank_service import (
    VertexAiMemoryBankService,
)
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.load_memory_tool import LoadMemoryTool
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.tools.vertex_ai_load_profiles_tool import (
    VertexAiLoadProfilesTool,
)
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

# CRITICAL: Hardcode the GCP project ID string, GCS Bucket Name, and Memory Bank Agent Engine ID.
# On Agent Platform, google.auth.default() and GOOGLE_CLOUD_PROJECT return the
# numeric project number, which causes Firestore and GCS database calls to fail.
PROJECT_ID = "qwiklabs-gcp-02-3fef644cbbdc"
BUCKET_NAME = "realm-weaver-assets-3fef644cbbdc"
MEMORY_BANK_ID = "3567569985481998336"

# Initialize GCP clients
db = firestore.Client(project=PROJECT_ID)
storage_client = storage.Client(project=PROJECT_ID)
genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")

# Initialize Memory Bank Service for profile and session memory persistence
memory_service = VertexAiMemoryBankService(
    project=PROJECT_ID,
    location="us-east1",
    agent_engine_id=MEMORY_BANK_ID,
)

# Initialize Agent Engine Sandbox Code Executor using deployment_metadata.json
deployment_metadata_path = Path(__file__).parent.parent / "deployment_metadata.json"
agent_engine_resource_name = f"projects/193044989679/locations/us-east1/reasoningEngines/{MEMORY_BANK_ID}"
sandbox_resource_name = None

if deployment_metadata_path.exists():
    try:
        with open(deployment_metadata_path, "r") as f:
            meta = json.load(f)
            if meta.get("remote_agent_runtime_id"):
                agent_engine_resource_name = meta.get("remote_agent_runtime_id")
            if meta.get("sandbox_resource_name"):
                sandbox_resource_name = meta.get("sandbox_resource_name")
    except Exception:
        pass

code_executor = AgentEngineSandboxCodeExecutor(
    sandbox_resource_name=sandbox_resource_name,
    agent_engine_resource_name=agent_engine_resource_name,
)

# Build A2UI system prompt using A2uiSchemaManager v0.8 and Basic Catalog v0.8
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are Realm Weaver, a Master Dungeon Master for tabletop RPG adventures."
    ),
    workflow_description=(
        "Analyze tabletop RPG requests, spell queries, monster stat lookups, roll results, and character preferences, "
        "and return structured UI (cards, rows, columns, text, images) when appropriate. "
        "Pay special attention to all user allergies, dietary restrictions, and personal preferences. Always remember, load, and strictly respect any user allergies mentioned or stored in memory. "
        "Use your memory tools (PreloadMemoryTool, LoadMemoryTool, VertexAiLoadProfilesTool) to load user memories and allergy profiles, "
        "use your code executor to safely run Python code for complex math or simulation when requested, "
        "use generate_item_image to create visual concept art for RPG items, weapons, and artifacts, "
        "use generate_item_video to create animated video clips for RPG items, weapons, and artifacts, "
        "use geocode_address and find_nearby_places to translate real-world locations into campaign settings, "
        "use lookup_dnd_spell to query official D&D 5e spell rules, use roll_dice to roll dice for checks/attacks, "
        "and use your bestiary tools to query, list, or record monsters and encounters."
    ),
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


async def generate_item_image(
    prompt: str,
    tool_context: ToolContext,
) -> str:
    """Generates an image for a RPG item, weapon, or artifact using gemini-3.1-flash-lite-image in the global region.
    Saves the image as a session artifact and uploads it to public Cloud Storage.

    Args:
        prompt: Description of the RPG item or object to illustrate (e.g. 'A glowing blue longsword with dragon runes').
        tool_context: ToolContext instance automatically provided by the ADK framework.

    Returns:
        The public HTTPS URL of the generated image.
    """
    try:
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=f"Fantasy RPG item concept art: {prompt}",
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        image_bytes = None
        mime_type = "image/png"
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type
                    break

        if not image_bytes:
            return "Failed to generate image: no image data returned from model."

        ext = "png" if "png" in mime_type else "jpg"
        filename = f"item_{uuid.uuid4().hex[:8]}.{ext}"

        # 1. Save artifact to Playground UI via ToolContext
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload image bytes directly to public Cloud Storage bucket
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return f"Successfully generated image. Public URL: {public_url}"

    except Exception as e:
        return f"Error generating item image: {e}"


async def generate_item_video(
    prompt: str,
    tool_context: ToolContext,
) -> str:
    """Generates a short video for a RPG item, weapon, or artifact using gemini-omni-flash-preview in the global region.
    Saves the video as a session artifact and uploads it to public Cloud Storage.

    Args:
        prompt: Description of the RPG item or object video to illustrate (e.g. 'A short video of a glowing magic wand releasing sparkles').
        tool_context: ToolContext instance automatically provided by the ADK framework.

    Returns:
        The public HTTPS URL of the generated video.
    """
    try:
        response = genai_client.models.generate_content(
            model="gemini-omni-flash-preview",
            contents=f"Short fantasy RPG video animation: {prompt}",
            config=types.GenerateContentConfig(
                response_modalities=["VIDEO"],
            ),
        )

        video_bytes = None
        mime_type = "video/mp4"
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    video_bytes = part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type
                    break

        if not video_bytes:
            return "Failed to generate video: no video data returned from model."

        ext = "mp4"
        if "webm" in mime_type:
            ext = "webm"
        filename = f"item_video_{uuid.uuid4().hex[:8]}.{ext}"

        # 1. Save artifact to Playground UI via ToolContext
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload video bytes directly to public Cloud Storage bucket
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return f"Successfully generated video. Public URL: {public_url}"

    except Exception as e:
        return f"Error generating item video: {e}"


def geocode_address(address: str) -> dict[str, Any] | str:
    """Converts a street address or location name into geographic coordinates (latitude and longitude).

    Args:
        address: The address or place name to geocode (e.g., '1600 Amphitheatre Pkwy, Mountain View, CA' or 'Empire State Building').

    Returns:
        A dictionary containing the formatted address and location coordinates (lat, lng), or an error message.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "GOOGLE_MAPS_API_KEY environment variable is missing."

    encoded_address = urllib.parse.quote(address)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("status") == "OK" and data.get("results"):
                result = data["results"][0]
                return {
                    "address": result.get("formatted_address"),
                    "location": result.get("geometry", {}).get("location"),
                }
            return f"Geocoding failed for address '{address}': {data.get('status')}"
    except Exception as e:
        return f"Error calling Geocoding API: {e}"


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "tourist_attraction",
    radius: float = 1000.0,
) -> list[dict[str, Any]] | str:
    """Finds nearby places of a specified type around geographic coordinates using the Google Places API (New).

    Args:
        latitude: Latitude coordinate of the central location.
        longitude: Longitude coordinate of the central location.
        place_type: Type of place to search for (e.g. 'restaurant', 'park', 'museum', 'tourist_attraction').
        radius: Radius in meters to search within (default 1000m).

    Returns:
        A list of place dictionaries containing name, address, and location.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "GOOGLE_MAPS_API_KEY environment variable is missing."

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }
    payload = {
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": radius,
            }
        },
    }

    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            places = data.get("places", [])
            results = []
            for place in places:
                results.append({
                    "name": place.get("displayName", {}).get("text"),
                    "address": place.get("formattedAddress"),
                    "location": place.get("location"),
                })
            return results
    except Exception as e:
        return f"Error calling Places API (New): {e}"


def lookup_dnd_spell(spell_name: str) -> dict[str, Any] | str:
    """Looks up official D&D 5e spell details (level, range, casting time, description) from the free D&D 5e Public API.

    Args:
        spell_name: The name of the spell (e.g., 'fireball', 'magic-missile', 'cure-wounds', 'shield').

    Returns:
        A dictionary with spell information or an error message if not found.
    """
    formatted_name = spell_name.lower().strip().replace(" ", "-")
    url = f"https://www.dnd5eapi.co/api/spells/{formatted_name}"

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "RealmWeaverAgent/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                return {
                    "name": data.get("name"),
                    "level": data.get("level"),
                    "range": data.get("range"),
                    "casting_time": data.get("casting_time"),
                    "duration": data.get("duration"),
                    "components": data.get("components"),
                    "description": " ".join(data.get("desc", [])),
                }
    except Exception as e:
        return f"Could not retrieve spell '{spell_name}' from D&D 5e API: {e}"

    return f"Spell '{spell_name}' was not found in the D&D 5e API."


def roll_dice(dice_expression: str) -> str:
    """Rolls polyhedral RPG dice using standard notation (e.g. '1d20+5', '3d6', 'd8-1').

    Args:
        dice_expression: Standard dice notation (e.g., '1d20+5', '3d6', 'd20', '2d8-2').

    Returns:
        A string summarizing individual rolls and the calculated total result.
    """
    pattern = r"^\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*$"
    match = re.match(pattern, dice_expression.lower())
    if not match:
        return f"Invalid dice expression '{dice_expression}'. Use format like '1d20+5', '3d6', or 'd8'."

    num_dice_str, sides_str, modifier_str = match.groups()
    num_dice = int(num_dice_str) if num_dice_str else 1
    sides = int(sides_str)
    modifier = int(modifier_str.replace(" ", "")) if modifier_str else 0

    if num_dice <= 0 or sides <= 0 or num_dice > 100:
        return "Number of dice must be between 1 and 100 with sides > 0."

    rolls = [random.randint(1, sides) for _ in range(num_dice)]
    total = sum(rolls) + modifier

    rolls_str = ", ".join(map(str, rolls))
    mod_str = f" {modifier_str.strip()}" if modifier_str else ""
    return f"Rolled {dice_expression}: [{rolls_str}]{mod_str} = Total: {total}"


def list_monsters() -> list[dict[str, Any]]:
    """Retrieves all monsters stored in the bestiary database.

    Returns:
        A list of dictionaries representing the monsters and their stats.
    """
    monsters_ref = db.collection("monsters")
    docs = monsters_ref.stream()
    return [doc.to_dict() for doc in docs]


def get_monster(name_or_id: str) -> dict[str, Any] | str:
    """Looks up a specific monster in the bestiary by name or ID.

    Args:
        name_or_id: The ID or name of the monster (e.g. 'beholder' or 'Ancient Red Dragon').

    Returns:
        A dictionary containing monster stats or an error message if not found.
    """
    search = name_or_id.lower().strip()

    # Check exact document ID first
    doc_ref = db.collection("monsters").document(search)
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()

    # Search by name or ID field
    docs = db.collection("monsters").stream()
    for d in docs:
        m = d.to_dict()
        if (
            m.get("id", "").lower() == search
            or m.get("name", "").lower() == search
            or search in m.get("name", "").lower()
        ):
            return m

    return f"Monster '{name_or_id}' was not found in the bestiary."


def add_monster(
    id: str,
    name: str,
    type: str,
    cr: float,
    hp: int,
    ac: int,
    description: str,
    abilities: list[str],
) -> str:
    """Adds a new monster or updates an existing entry in the bestiary database.

    Args:
        id: Unique identifier for the monster (e.g., 'frost_giant').
        name: Display name of the monster (e.g., 'Frost Giant').
        type: Creature type (e.g., 'Giant', 'Dragon', 'Undead').
        cr: Challenge Rating (e.g., 8.0, 0.25).
        hp: Hit Points (e.g., 138).
        ac: Armor Class (e.g., 15).
        description: Description of the monster's appearance and behavior.
        abilities: List of special attacks or abilities.

    Returns:
        A confirmation message.
    """
    monster_data = {
        "id": id,
        "name": name,
        "type": type,
        "cr": cr,
        "hp": hp,
        "ac": ac,
        "description": description,
        "abilities": abilities,
    }
    db.collection("monsters").document(id).set(monster_data)
    return f"Successfully saved monster '{name}' (ID: {id}) to the bestiary."


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    after_model_callback=a2ui_callback,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        LoadMemoryTool(),
        VertexAiLoadProfilesTool(memory_service=memory_service),
        generate_item_image,
        generate_item_video,
        geocode_address,
        find_nearby_places,
        lookup_dnd_spell,
        roll_dice,
        list_monsters,
        get_monster,
        add_monster,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
