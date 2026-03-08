from framework.data_source import CensusDataSource
import pandas as pd

def map_census_to_ontology(onto):
    """
    Hydrates the ontology with State and County data from the Census API.
    """
    print("Fetching State data...")
    state_source = CensusDataSource("states")
    states_data = state_source.fetch()

    # Process states data (skipping the first row which is the header)
    header = states_data[0]
    data = states_data[1:]
    df_states = pd.DataFrame(data, columns=header)

    with onto:
        pop_col = 'P1_001N' # Updated for 2020 Decennial Census
        for index, row in df_states.iterrows():
            state_name = row['NAME']
            state_fips = row['state']

            # Create State individual
            state_uri = f"State_{state_fips}"
            state = onto.State(state_uri)
            state.hasName = state_name
            state.hasFIPS = state_fips
            state.hasPopulation = int(row[pop_col])

            print(f"Added State: {state_name} ({state_fips})")

            # For demonstration, only fetch counties for a few states (e.g., NJ and NY)
            if state_fips in ['34', '36']: # NJ and NY FIPS
                print(f"  Fetching County data for {state_name}...")
                county_source = CensusDataSource(f"counties_{state_fips}", state_fips=state_fips)
                counties_data = county_source.fetch()

                c_header = counties_data[0]
                c_data = counties_data[1:]
                df_counties = pd.DataFrame(c_data, columns=c_header)

                for c_index, c_row in df_counties.iterrows():
                    county_name = c_row['NAME']
                    county_fips = c_row['county']
                    full_fips = state_fips + county_fips

                    # Create County individual
                    county_uri = f"County_{full_fips}"
                    county = onto.County(county_uri)
                    county.hasName = county_name
                    county.hasFIPS = full_fips
                    county.hasPopulation = int(c_row[pop_col])

                    # Relationship
                    county.isPartOf = [state]

                    # print(f"    Added County: {county_name} ({full_fips})")

    print("Census hydration complete.")
