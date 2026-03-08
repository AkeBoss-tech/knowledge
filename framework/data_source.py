import os
import requests
import pandas as pd
import json
from pathlib import Path

class DataSource:
    def __init__(self, name, cache_dir="cache"):
        self.name = name
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, force=False):
        """Fetch data from the source, using cache if available."""
        raise NotImplementedError

class CSVDataSource(DataSource):
    def __init__(self, name, filepath):
        super().__init__(name)
        self.filepath = filepath

    def fetch(self, force=False):
        return pd.read_csv(self.filepath)

class ExcelDataSource(DataSource):
    def __init__(self, name, filepath):
        super().__init__(name)
        self.filepath = filepath

    def fetch(self, force=False):
        return pd.read_excel(self.filepath)

class APIDataSource(DataSource):
    def __init__(self, name, url, params=None, cache_filename=None):
        super().__init__(name)
        self.url = url
        self.params = params or {}
        self.cache_filename = cache_filename or f"{self.name}.json"
        self.cache_path = self.cache_dir / self.cache_filename

    def fetch(self, force=False):
        if self.cache_path.exists() and not force:
            print(f"Loading {self.name} from cache...")
            with open(self.cache_path, 'r') as f:
                return json.load(f)

        print(f"Fetching {self.name} from API: {self.url} with params {self.params}")
        response = requests.get(self.url, params=self.params)
        if response.status_code != 200:
            print(f"Error fetching from API: {response.status_code} - {response.text}")
        response.raise_for_status()
        data = response.json()

        with open(self.cache_path, 'w') as f:
            json.dump(data, f)

        return data

class CensusDataSource(APIDataSource):
    """Specific implementation for US Census API."""
    def __init__(self, name, state_fips=None, county_fips=None):
        # Example URL for population data (can be customized)
        # Using 2021 ACS 1-Year estimates or similar if PEP has issues
        # Actually, let's try 2020 Decennial Census for better stability in demo
        url = "https://api.census.gov/data/2020/dec/pl"
        params = {
            "get": "NAME,P1_001N", # P1_001N is total population in 2020 PL94-171
            "for": "state:*"
        }
        if state_fips:
            params["for"] = "county:*"
            params["in"] = f"state:{state_fips}"

        super().__init__(name, url, params, cache_filename=f"census_{name}.json")

class FREDDataSource(APIDataSource):
    """Specific implementation for St. Louis Fed FRED API."""
    def __init__(self, name, series_id, api_key="placeholder"):
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json"
        }
        super().__init__(name, url, params, cache_filename=f"fred_{name}.json")
