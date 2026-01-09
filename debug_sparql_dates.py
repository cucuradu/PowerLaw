import requests
import json

def check_sparql_dates():
    endpoint = "https://publications.europa.eu/webapi/rdf/sparql"
    query = """
    PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    
    SELECT ?celex ?end_date WHERE {
        VALUES ?celex { "32025R2067" "31990L0492" }
        ?work cdm:resource_legal_id_celex ?celex .
        OPTIONAL { ?work cdm:resource_legal_date_end-of-validity ?end_date . }
    }
    """
    
    try:
        r = requests.get(endpoint, params={"query": query}, headers={"Accept": "application/sparql-results+json"})
        data = r.json()
        
        for result in data["results"]["bindings"]:
            celex = result["celex"]["value"]
            end_date = result.get("end_date", {}).get("value", "None")
            print(f"CELEX: {celex} | End Date: {end_date}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_sparql_dates()

if __name__ == "__main__":
    check_sparql_dates()
