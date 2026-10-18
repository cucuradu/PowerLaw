import requests

def debug_props():
    endpoint = "https://publications.europa.eu/webapi/rdf/sparql"
    celex = "32024R0745"
    
    query = f"""
    PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
    SELECT DISTINCT ?p ?o WHERE {{
        ?s cdm:resource_legal_id_celex ?celex .
        FILTER(REGEX(?celex, "{celex}"))
        ?s ?p ?o .
        FILTER(REGEX(STR(?p), "concept|subject", "i"))
    }}
    LIMIT 50
    """
    
    try:
        r = requests.get(endpoint, params={"query": query}, headers={"Accept": "application/sparql-results+json"})
        data = r.json()
        print(f"Props for {celex}:")
        for b in data['results']['bindings']:
            print(f" {b['p']['value']} -> {b['o']['value']}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_props()
