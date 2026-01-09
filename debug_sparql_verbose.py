import requests
import json

def check_sparql_dates():
    endpoint = "https://publications.europa.eu/webapi/rdf/sparql"
    # Simplified query without VALUES first to see if connectivity works
    query = """
    PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
    
    SELECT ?celex ?date WHERE {
      ?work cdm:resource_legal_id_celex ?celex .
      ?work cdm:work_date_document ?date .
      FILTER (?celex = "32025R2067")
    }
    LIMIT 5
    """
    
    print(f"Querying {endpoint}...")
    try:
        r = requests.get(endpoint, params={"query": query}, headers={"Accept": "application/sparql-results+json"}, timeout=10)
        print(f"Status: {r.status_code}")
        
        if r.status_code == 200:
            data = r.json()
            bindings = data["results"]["bindings"]
            print(f"Results found: {len(bindings)}")
            for b in bindings:
                print(b)
        else:
            print(r.text)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_sparql_dates()
