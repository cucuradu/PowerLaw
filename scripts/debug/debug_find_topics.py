import requests

def search_eurovoc(label_or_id):
    endpoint = "https://publications.europa.eu/webapi/rdf/sparql"
    # If digit, search by ID. If text, search by label.
    if label_or_id.isdigit():
        query = f"""
        PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
        SELECT ?label WHERE {{
            ?c skos:prefLabel ?label .
            FILTER(LANG(?label) = "en")
            FILTER(REGEX(STR(?c), "{label_or_id}$"))
        }}
        """
    else:
        query = f"""...""" # (Keep label logic if needed, but we focus on ID now)
    
    # Simple ID lookup
    query = f"""
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    SELECT ?label WHERE {{
        ?c skos:prefLabel ?label .
        FILTER(LANG(?label) = "en")
        FILTER(REGEX(STR(?c), "eurovoc/{label_or_id}$"))
    }}
    LIMIT 1
    """

    try:
        r = requests.get(endpoint, params={"query": query}, headers={"Accept": "application/sparql-results+json"})
        data = r.json()
        bindings = data['results']['bindings']
        if bindings:
             print(f"{label_or_id}: {bindings[0]['label']['value']}")
        else:
             print(f"{label_or_id}: ???")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    labels = ["anti-dumping", "import duty", "fisheries", "agriculture", "food safety"]
    for l in labels:
        search_eurovoc(l)
