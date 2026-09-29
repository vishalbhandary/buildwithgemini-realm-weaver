import os
from google.cloud import firestore

# CRITICAL: Hardcode the project ID as a string.
# Do NOT use google.auth.default() or GOOGLE_CLOUD_PROJECT, as those return the
# numeric project number on Agent Platform which breaks Firestore database resolution.
PROJECT_ID = "qwiklabs-gcp-02-3fef644cbbdc"

def seed_monsters():
    print(f"Connecting to Firestore with project ID: {PROJECT_ID}...")
    db = firestore.Client(project=PROJECT_ID)
    
    monsters_ref = db.collection("monsters")
    
    seed_data = [
        {
            "id": "red_dragon",
            "name": "Ancient Red Dragon",
            "type": "Dragon",
            "cr": 24,
            "hp": 546,
            "ac": 22,
            "description": "A colossal winged reptile clad in crimson scales, breathing devastating fire.",
            "abilities": ["Fire Breath", "Frightful Presence", "Multiattack", "Wing Attack"]
        },
        {
            "id": "goblin_scout",
            "name": "Goblin Scout",
            "type": "Humanoid",
            "cr": 0.25,
            "hp": 7,
            "ac": 13,
            "description": "A small, crafty humanoid armed with a rusty scimitar and shortbow.",
            "abilities": ["Nimble Escape", "Scimitar Attack", "Shortbow Attack"]
        },
        {
            "id": "beholder",
            "name": "Beholder",
            "type": "Aberration",
            "cr": 13,
            "hp": 180,
            "ac": 18,
            "description": "A floating orb dominated by a central eye and ten eyestalks shooting magical rays.",
            "abilities": ["Antimagic Cone", "Eye Rays", "Bite Attack"]
        },
        {
            "id": "skeleton_warrior",
            "name": "Skeleton Warrior",
            "type": "Undead",
            "cr": 0.25,
            "hp": 13,
            "ac": 13,
            "description": "An animated skeletal remains holding a notched shortsword and worn shield.",
            "abilities": ["Shortsword Attack", "Darkvision"]
        },
        {
            "id": "mind_flayer",
            "name": "Mind Flayer",
            "type": "Aberration",
            "cr": 7,
            "hp": 71,
            "ac": 15,
            "description": "A dark, tentacled humanoid adept at psionic control and devouring intellects.",
            "abilities": ["Mind Blast", "Tentacles", "Extract Brain", "Dominate Monster"]
        }
    ]
    
    for monster in seed_data:
        doc_id = monster["id"]
        monsters_ref.document(doc_id).set(monster)
        print(f"  - Seeded monster '{monster['name']}' (ID: {doc_id})")
        
    print("Firestore seeding completed successfully!")

if __name__ == "__main__":
    seed_monsters()
