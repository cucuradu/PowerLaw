import requests
import json

SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
query = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT DISTINCT ?celex ?title ?exp_title
WHERE {
  ?work cdm:resource_legal_id_celex ?celex .
  ?work cdm:work_date_document ?date .
  FILTER (?date >= "2015-01-01"^^xsd:date && ?date <= "2015-01-05"^^xsd:date)
  FILTER (STRSTARTS(STR(?celex), "3"))
  FILTER (REGEX(?celex, "^3[0-9]{4}[RL][0-9]{4}"))

  OPTIONAL { ?work cdm:work_title ?title . }
  OPTIONAL { 
    ?work cdm:work_has_expression ?exp .
    ?exp cdm:expression_language ?lang .
    ?exp cdm:expression_title ?exp_title .
    # Just Dump all langs to see what's available
  }
}
LIMIT 5
"""

headers = {"Accept": "application/sparql-results+json"}
r = requests.get(SPARQL_ENDPOINT, params={"query": query}, headers=headers)
print(json.dumps(r.json(), indent=2))
