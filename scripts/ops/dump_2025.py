import requests

def dump_2025():
    endpoint = "https://publications.europa.eu/webapi/rdf/sparql"
    query = """
    PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    
    SELECT ?celex WHERE {
      ?work cdm:resource_legal_id_celex ?celex .
      ?work cdm:work_date_document ?date .
      FILTER (?date >= "2025-01-01"^^xsd:date)
      FILTER (REGEX(?celex, "^3[0-9]{4}[RL][0-9]{4}"))
    }
    LIMIT 100
    """
    
    try:
        r = requests.get(endpoint, params={"query": query}, headers={"Accept": "application/sparql-results+json"})
        data = r.json()
        print(f"Found {len(data['results']['bindings'])} laws in 2025.")
        for b in data['results']['bindings']:
            print(b['celex']['value'])
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    dump_2025()
