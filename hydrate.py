from owlready2 import *
from mappings.census_mapping import map_census_to_ontology
from mappings.csv_mapping import map_csv_to_ontology
import os

def main():
    # Define paths
    onto_path = "ontology/core_ontology.owl"
    db_path = "ontology/onto.db"
    csv_path = "sources/sample_individuals.csv"

    # Initialize the quadstore
    default_world.set_backend(filename=db_path)

    # Load the existing ontology
    # Use the file path directly to ensure it loads from local
    onto = get_ontology(f"file://{onto_path}").load()

    print("Starting hydration...")

    # 1. Hydrate from Census API
    map_census_to_ontology(onto)

    # 2. Hydrate from CSV
    if os.path.exists(csv_path):
        map_csv_to_ontology(onto, csv_path)

    # 3. Optional: Run reasoner (skipping due to Java version mismatch in environment)
    # print("Running reasoner...")
    # with onto:
    #     sync_reasoner_pellet(infer_property_values=True, infer_data_property_values=True)

    # 4. Save the populated ontology and quadstore
    onto.save(file="ontology/populated_ontology.owl", format="rdfxml")
    default_world.save()

    print(f"Hydration complete. Populated ontology saved to ontology/populated_ontology.owl")
    print(f"Quadstore updated at {db_path}")

if __name__ == "__main__":
    main()
