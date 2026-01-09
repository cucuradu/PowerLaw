try:
    from src.schemas.lancedb import LawChunk
    print("Import successful")
    print(LawChunk.model_json_schema())
except Exception as e:
    import traceback
    traceback.print_exc()
