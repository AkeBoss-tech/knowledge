import os
import sys

def verify_ontology(file_path):
    print(f"Verifying {file_path}...")
    if not os.path.exists(file_path):
        print("ERROR: File not found")
        return False
        
    content = open(file_path, "r").read()
    
    # 1. Check for basic Turtle structure (prefixes)
    if "@prefix" not in content:
        print("ERROR: No prefixes found")
        return False
        
    # 2. Check for key classes from design notes
    required_classes = [
        "uez:UEZMunicipality",
        "uez:NonUEZMunicipality",
        "uez:PreReformPeriod",
        "uez:PostReformPeriod",
        "uez:EmploymentObservation",
        "uez:SalesTaxObservation"
    ]
    
    missing = []
    for cls in required_classes:
        if cls not in content:
            missing.append(cls)
            
    if missing:
        print(f"ERROR: Missing required classes: {', '.join(missing)}")
        return False
        
    # 3. Check for property restrictions mentioned
    if "uez:observedDuring" not in content:
        print("ERROR: Missing uez:observedDuring property")
        return False
        
    print("SUCCESS: Ontology verification passed!")
    return True

if __name__ == "__main__":
    path = "generated_projects/assessing-the-economic-impact-of-the-urban-enterprise-zone-program-reform/.ontology/ontologies/uez-impact-ontology.ttl"
    if not verify_ontology(path):
        sys.exit(1)
