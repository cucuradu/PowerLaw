def debug_exact_logic():
    print("Running execute_sparql for 2017 & 2018 checks...")
    # We can fetch specific CELEXs directly via a targeted query function or just reuse the bulk one if fast enough.
    # Let's verify via direct query to be precise about what SPARQL returns for THEM.
    
    endpoint = "https://publications.europa.eu/webapi/rdf/sparql"
    targets = ["32024R0745", "32024L1260", "32024R1301", "32024R1721"]
    
    # Updated Topic List (Merged)
    TARGET_TOPICS = [
        # Original
        "5806", "5207", "5211", "6821", "2036", "4809", 
        "3633", "3634", "5824", "6816", "4308", "2441",
        "4031", "3236", "6006", "6806", "6811", "6826", "2446", "3131",
        # Environment
        "2825", "5463", "5315", "862", "5794", "3558", "763", "2807",
        # Expanded (Consumer, Social, Customs, Digital, Banking, Transport)
        "2836", "2517", "3346", "3156", "4380", "4600",
        "7219", "c_6f7bbe63", "4404", "3251", "2447", "c_c565cff5",
        "2457", "2494", "4509"
    ]
    
    for celex in targets:
        query = f"""
        PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
        SELECT ?celex (GROUP_CONCAT(?eurovoc_uri;separator="|") as ?topics) WHERE {{
            ?work cdm:resource_legal_id_celex "{celex}" .
            OPTIONAL {{ ?work cdm:work_is_about_concept_eurovoc ?eurovoc_uri . }}
        }}
        GROUP BY ?celex
        """
        import requests
        try:
            r = requests.get(endpoint, params={"query": query}, headers={"Accept": "application/sparql-results+json"})
            data = r.json()
            bindings = data['results']['bindings']
            if not bindings:
                print(f"❌ {celex} not found in SPARQL.")
                continue
                
            topics_str = bindings[0].get('topics', {}).get('value', "")
            topics = topics_str.split("|") if topics_str else []
            
            print(f"\n🔍 Analysis for {celex}:")
            print(f"   Topics Found: {topics}")
            
            matched = []
            for t in topics:
                tid = t.split("/")[-1]
                if tid in TARGET_TOPICS:
                    matched.append(tid)
            
            if matched:
                print(f"   ✅ Would be ACCEPTED by filter. Matched: {matched}")
            else:
                print(f"   ❌ Would be REJECTED by filter.")
                print("   👉 Relevant topics missing from our list:")
                for t in topics:
                    print(f"      - {t} (ID: {t.split('/')[-1]})")
                    
        except Exception as e:
            print(f"Error {e}")

if __name__ == "__main__":
    debug_exact_logic()
