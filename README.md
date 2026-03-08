# Rutgers Agentic Intelligence Labs
An ontology and AI-enhanced framework for economic analysis, inspired by Palantir Foundry's Ontology layer.

## Project Structure
- `ontology/`: Contains the core ontology definition and the SQLite quadstore.
- `framework/`: Generalized data hydration framework (CSV, Excel, API sources).
- `mappings/`: Domain-specific mapping logic (e.g., Census API to Ontology).
- `sources/`: Static data sources (CSV files).
- `cache/`: Cached API responses.
- `app.py`: Streamlit-based visualization explorer.
- `hydrate.py`: Main orchestration script for data ingestion.

## Setup & Running

1. **Install Dependencies**:
   ```bash
   pip install owlready2 pandas streamlit pyvis requests openpyxl rdflib
   ```

2. **Initialize & Hydrate Data**:
   This script loads the ontology, fetches data from the US Census API (States and Counties), reads sample individuals from a CSV, and populates the quadstore.
   ```bash
   python hydrate.py
   ```

3. **Explore the Ontology**:
   Start the Streamlit app to browse entities and visualize relationships.
   ```bash
   streamlit run app.py
   ```

## Domain Model
The current implementation focuses on geographical and demographic data:
- **Classes**: `State`, `County`, `Municipality`, `Individual`.
- **Properties**: `isPartOf`, `hasPart`, `locatedIn`, `hasPopulation`, `hasName`, `hasFIPS`, `hasIncome`.

## Features
- **Semantic Layer**: Knowledge graph built with `Owlready2`.
- **Flexible Hydration**: Abstract `DataSource` classes for easy extension to new APIs or file formats.
- **Graph Visualization**: Interactive relationship exploration with `pyvis` and `Streamlit`.
- **Persistent Storage**: Persistent SQLite quadstore for efficient querying.
