import requests

def debug_status(celex_id):
    url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex_id}"
    print(f"Checking URL: {url}")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        text = r.text.lower()
        
        print(f"Status Code: {r.status_code}")
        
        triggers = ["no longer in force", "repealed", "expired"]
        found = []
        for t in triggers:
            if t in text:
                found.append(t)
                
        if found:
            print(f"❌ Detects ARCHIVED. Triggers found: {found}")
            # Print context
            idx = text.find(found[0])
            start = max(0, idx - 100)
            end = min(len(text), idx + 100)
            print(f"Context: ...{text[start:end]}...")
        else:
            print("✅ Detects IN FORCE (No triggers found)")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_status("32025R2067")
