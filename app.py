import streamlit as st
from owlready2 import *
from pyvis.network import Network
import streamlit.components.v1 as components
import os

# Set page config
st.set_page_config(page_title="Ontology Explorer", layout="wide")

st.title("Rutgers Ontology & AI Framework Explorer")
st.sidebar.title("Settings")

# Load ontology
db_path = "ontology/onto.db"
if os.path.exists(db_path):
    default_world.set_backend(filename=db_path)
    onto = get_ontology("http://example.org/rutgers_ontology.owl").load()
else:
    st.error("Ontology database not found. Please run hydrate.py first.")
    st.stop()

# Sidebar: Search & Filter
st.sidebar.header("Search")
search_term = st.sidebar.text_input("Search entities (e.g. 'Alice', 'New Jersey')")

# Main Content
col1, col2 = st.columns([1, 3])

with col1:
    st.header("Entities")
    entity_type = st.selectbox("Select Type", ["All", "State", "County", "Municipality", "Individual"])

    entities = []
    if entity_type == "All":
        entities = list(onto.individuals())
    elif entity_type == "State":
        entities = list(onto.State.instances())
    elif entity_type == "County":
        entities = list(onto.County.instances())
    elif entity_type == "Municipality":
        entities = list(onto.Municipality.instances())
    elif entity_type == "Individual":
        entities = list(onto.Individual.instances())

    if search_term:
        entities = [e for e in entities if search_term.lower() in str(e.name).lower() or (hasattr(e, 'hasName') and e.hasName and search_term.lower() in e.hasName.lower())]

    selected_entity_name = st.selectbox("Select Entity to Explore", [e.name for e in entities])

    selected_entity = None
    if selected_entity_name:
        selected_entity = onto[selected_entity_name]

    if selected_entity:
        st.subheader("Properties")
        st.write(f"**URI:** {selected_entity.iri}")
        if hasattr(selected_entity, 'hasName') and selected_entity.hasName:
            st.write(f"**Name:** {selected_entity.hasName}")
        if hasattr(selected_entity, 'hasPopulation') and selected_entity.hasPopulation:
            st.write(f"**Population:** {selected_entity.hasPopulation:,}")
        if hasattr(selected_entity, 'hasIncome') and selected_entity.hasIncome:
            st.write(f"**Income:** ${selected_entity.hasIncome:,.2f}")
        if hasattr(selected_entity, 'hasFIPS') and selected_entity.hasFIPS:
            st.write(f"**FIPS:** {selected_entity.hasFIPS}")

        st.subheader("Relationships")
        # Show outgoing relations
        for prop in selected_entity.get_properties():
            for value in prop[selected_entity]:
                st.write(f"**{prop.python_name}:** {value.name if hasattr(value, 'name') else value}")

with col2:
    st.header("Relationship Graph")

    if selected_entity:
        # Create pyvis network
        net = Network(height="600px", width="100%", bgcolor="#222222", font_color="white", directed=True)

        # Add central node
        net.add_node(selected_entity.name, label=selected_entity.hasName or selected_entity.name, color="#ff4b4b", size=30)

        # Add neighbors (1 level deep)
        for prop in selected_entity.get_properties():
            values = prop[selected_entity]
            if not isinstance(values, list): values = [values]
            for value in values:
                if hasattr(value, 'name'):
                    net.add_node(value.name, label=getattr(value, 'hasName', value.name), color="#00acee")
                    net.add_edge(selected_entity.name, value.name, label=prop.python_name)

        # Add reverse relations (who points to this)
        for rel in selected_entity.get_inverse_properties():
            for source, prop in rel:
                 if hasattr(source, 'name'):
                    net.add_node(source.name, label=getattr(source, 'hasName', source.name), color="#00acee")
                    net.add_edge(source.name, selected_entity.name, label=rel.python_name)

        # Save and render
        net.save_graph("graph.html")
        HtmlFile = open("graph.html", 'r', encoding='utf-8')
        source_code = HtmlFile.read()
        components.html(source_code, height=600)
    else:
        st.info("Select an entity on the left to visualize its relationships.")

st.markdown("---")
st.caption("Rutgers Agentic Intelligence Labs - 2026")
