from owlready2 import *
import os

def create_ontology():
    # Define the ontology file path
    onto_path = "ontology/core_ontology.owl"
    db_path = "ontology/onto.db"

    # Initialize the quadstore
    default_world.set_backend(filename=db_path)

    # Create the ontology
    onto = get_ontology("http://example.org/rutgers_ontology.owl")

    with onto:
        # Define Classes
        class State(Thing): pass
        class County(Thing): pass
        class Municipality(Thing): pass
        class Individual(Thing): pass

        # Define Object Properties (Relationships)
        class isPartOf(ObjectProperty):
            domain = [County, Municipality]
            range = [State, County]

        class hasPart(ObjectProperty):
            inverse_property = isPartOf

        class locatedIn(ObjectProperty):
            domain = [Individual, Municipality, County]
            range = [Municipality, County, State]

        # Define Data Properties (Attributes)
        class hasName(DataProperty, FunctionalProperty):
            domain = [Thing]
            range = [str]

        class hasPopulation(DataProperty, FunctionalProperty):
            domain = [State, County, Municipality]
            range = [int]

        class hasFIPS(DataProperty, FunctionalProperty):
            domain = [State, County]
            range = [str]

        class hasIncome(DataProperty, FunctionalProperty):
            domain = [Individual]
            range = [float]

    # Save the ontology
    onto.save(file=onto_path, format="rdfxml")
    print(f"Ontology created and saved to {onto_path}")
    print(f"Quadstore initialized at {db_path}")

if __name__ == "__main__":
    create_ontology()
