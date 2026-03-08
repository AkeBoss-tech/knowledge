from framework.data_source import CSVDataSource
import pandas as pd

def map_csv_to_ontology(onto, filepath):
    """
    Hydrates the ontology with Individual data from a CSV file.
    """
    print(f"Loading Individuals from {filepath}...")
    source = CSVDataSource("individuals", filepath)
    df = source.fetch()

    with onto:
        for _, row in df.iterrows():
            individual_id = row['id']
            individual_name = row['name']
            municipality_name = row['municipality']
            income = float(row['income'])

            # Create Individual individual
            person = onto.Individual(individual_id)
            person.hasName = individual_name
            person.hasIncome = income

            # Find or create Municipality individual
            muni_uri = f"Municipality_{municipality_name.replace(' ', '_')}"
            muni = onto.Municipality(muni_uri)
            muni.hasName = municipality_name

            # Relationships
            person.locatedIn = [muni]

            # For demonstration, manually link NJ municipalities to some counties
            if municipality_name in ['Hoboken', 'Jersey City']:
                # Hudson County, NJ (34017)
                hudson = onto.search_one(iri="*County_34017")
                if hudson:
                    muni.isPartOf = [hudson]
            elif municipality_name == 'Newark':
                # Essex County, NJ (34013)
                essex = onto.search_one(iri="*County_34013")
                if essex:
                    muni.isPartOf = [essex]
            elif municipality_name == 'Albany':
                # Albany County, NY (36001)
                albany = onto.search_one(iri="*County_36001")
                if albany:
                    muni.isPartOf = [albany]
            elif municipality_name == 'Syracuse':
                # Onondaga County, NY (36067)
                onondaga = onto.search_one(iri="*County_36067")
                if onondaga:
                    muni.isPartOf = [onondaga]

            print(f"Added Individual: {individual_name} in {municipality_name}")

    print("CSV hydration complete.")
