import requests
import json

SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
query = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?exp ?lang ?title
WHERE {
  ?s cdm:resource_legal_id_celex "32015R0021"^^xsd:string .
  ?s cdm:work_has_expression ?exp .
  ?exp cdm:expression_language ?lang .
  OPTIONAL { ?exp cdm:expression_title ?title }
}
LIMIT 50
"""

headers = {"Accept": "application/sparql-results+json"}
r = requests.get(SPARQL_ENDPOINT, params={"query": query}, headers=headers)
print(json.dumps(r.json(), indent=2))
