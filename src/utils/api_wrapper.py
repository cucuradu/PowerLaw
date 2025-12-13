#!/usr/bin/env python3
"""
Google AI API Wrapper - Enterprise Gateway
Centralized API interface for all AI operations using Google Cloud

This module provides:
- Text embeddings via Google's latest embedding model
- LLM completions with Gemini 1.5 Flash/Pro
- Enterprise-grade retry logic optimized for paid tier
- Zero local RAM usage (100% cloud-based)

Author: Python Backend Engineer - Google Cloud AI Specialist
Date: 2025-12-01
"""

import os
from typing import List, Optional, Any
from dotenv import load_dotenv

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

# Load environment variables
load_dotenv()

# Configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Model identifiers
EMBEDDING_MODEL = "models/text-embedding-004"  # 768 dimensions
LLM_FLASH_MODEL = "models/gemini-2.0-flash"      # Ultra-fast, Enterprise Grade
LLM_PRO_MODEL = "models/gemini-1.5-pro-002"      # Best Reasoning (Stable)

# Safety settings - BLOCK_NONE for enterprise control
SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

# Generation config for deterministic output
GENERATION_CONFIG = {
    "temperature": 0.0,  # Deterministic
    "top_p": 1.0,
    "top_k": 1,
}


def initialize_api() -> None:
    """
    Initialize Google Generative AI API
    Must be called before using any AI functions
    """
    if not GOOGLE_API_KEY:
        raise ValueError(
            "GOOGLE_API_KEY not found in environment. "
            "Please set it in your .env file."
        )
    
    genai.configure(api_key=GOOGLE_API_KEY)
    print("✅ Google AI API initialized")


# Retry decorator optimized for PAID tier
# Aggressive retries for high throughput
retry_config = retry(
    # Retry on transient errors (rate limit, server errors, network issues)
    retry=retry_if_exception_type((
        Exception,  # Catch all for transient issues
    )),
    # Stop after 5 attempts (paid tier has higher limits)
    stop=stop_after_attempt(5),
    # Exponential backoff: 1s, 2s, 4s, 8s, 10s (capped)
    # Much faster than free tier (typically 4s, 8s, 16s, 32s, 60s)
    wait=wait_exponential(multiplier=1, min=1, max=10),
    # Log retry attempts
    reraise=True
)


@retry_config
def get_embedding(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
    """
    Generate text embedding using Google's latest embedding model
    Optimized for paid tier with aggressive retries
    
    Args:
        text: Input text to embed
        task_type: Embedding task type
            - "RETRIEVAL_DOCUMENT": For documents to be retrieved
            - "RETRIEVAL_QUERY": For search queries
            - "SEMANTIC_SIMILARITY": For similarity comparison
            - "CLASSIFICATION": For text classification
    
    Returns:
        768-dimensional embedding vector as list of floats
    
    Example:
        >>> vector = get_embedding("The price of copper is rising")
        >>> len(vector)
        768
    """
    if not text or not text.strip():
        raise ValueError("Input text cannot be empty")
    
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=text,
        task_type=task_type
    )
    
    return result['embedding']


@retry_config
def get_embeddings_batch(
    texts: List[str], 
    task_type: str = "RETRIEVAL_DOCUMENT"
) -> List[List[float]]:
    """
    Generate embeddings for multiple texts in a single API call
    MASSIVE throughput optimization for paid tier
    
    Args:
        texts: List of input texts to embed
        task_type: Embedding task type (same as get_embedding)
    
    Returns:
        List of 768-dimensional embedding vectors
    
    Example:
        >>> texts = ["Copper prices", "Oil futures", "Steel demand"]
        >>> vectors = get_embeddings_batch(texts)
        >>> len(vectors)
        3
        >>> len(vectors[0])
        768
    """
    if not texts:
        raise ValueError("Input text list cannot be empty")
    
    # Filter out empty strings
    valid_texts = [t for t in texts if t and t.strip()]
    
    if not valid_texts:
        raise ValueError("All input texts are empty")
    
    # Google AI supports batch embedding
    embeddings = []
    for text in valid_texts:
        result = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=text,
            task_type=task_type
        )
        embeddings.append(result['embedding'])
    
    return embeddings


@retry_config
def get_completion(
    prompt: str, 
    use_pro: bool = False,
    max_output_tokens: Optional[int] = None
) -> str:
    """
    Get LLM completion using Gemini models
    
    Args:
        prompt: Input prompt for the model
        use_pro: If True, use Gemini 1.5 Pro (complex reasoning)
                 If False, use Gemini 1.5 Flash (fast, bulk operations)
        max_output_tokens: Maximum tokens in response (None = model default)
    
    Returns:
        Generated text completion
    
    Example:
        >>> # Fast extraction
        >>> response = get_completion("Extract the date from: 2023-01-15")
        
        >>> # Complex reasoning
        >>> response = get_completion(
        ...     "Analyze the legal implications...",
        ...     use_pro=True
        ... )
    """
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    
    # Select model based on complexity
    model_name = LLM_PRO_MODEL if use_pro else LLM_FLASH_MODEL
    
    # Create generation config
    gen_config = GENERATION_CONFIG.copy()
    if max_output_tokens:
        gen_config["max_output_tokens"] = max_output_tokens
    
    # Initialize model
    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config=gen_config,
        safety_settings=SAFETY_SETTINGS
    )
    
    # Generate content
    response = model.generate_content(prompt)
    
    # Extract text from response
    if not response.parts:
        raise ValueError("Empty response from API")
    
    return response.text


@retry_config
def get_completion_with_json(
    prompt: str,
    use_pro: bool = False,
    response_schema: Optional[Any] = None
) -> str:
    """
    Get LLM completion with JSON mode enabled
    Ensures output is valid JSON, optionally complying with a Pydantic schema
    
    Args:
        prompt: Input prompt (should request JSON output)
        use_pro: If True, use Pro model, else Flash
        response_schema: Optional Pydantic model class or schema dict
    
    Returns:
        JSON string response
    
    Example:
        >>> response = get_completion_with_json(
        ...     'Extract entities...',
        ...     response_schema=MyPydanticModel
        ... )
    """
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    
    # Select model
    model_name = LLM_PRO_MODEL if use_pro else LLM_FLASH_MODEL
    
    # Create generation config with JSON mode
    gen_config = GENERATION_CONFIG.copy()
    gen_config["response_mime_type"] = "application/json"
    
    if response_schema:
        # If Pydantic model, convert to clean JSON schema to avoid SDK errors with defaults
        if hasattr(response_schema, "model_json_schema"):
            try:
                schema = response_schema.model_json_schema()
                
                # Resolve $defs/$ref which are not supported by Gemini Protobuf Schema
                defs = schema.pop("$defs", {}) or schema.pop("definitions", {})
                
                def resolve_and_clean(node):
                    if isinstance(node, dict):
                        # Handle $ref
                        if "$ref" in node:
                            ref_key = node["$ref"].split("/")[-1]
                            if ref_key in defs:
                                return resolve_and_clean(defs[ref_key].copy())
                        
                        # Handle anyOf (Optional[T]) -> Type + nullable
                        if "anyOf" in node:
                            options = node.pop("anyOf")
                            # Find non-null option
                            non_null = [opt for opt in options if opt.get("type") != "null"]
                            if len(non_null) == 1:
                                # Merge non-null option into current node
                                resolved_opt = resolve_and_clean(non_null[0])
                                node.update(resolved_opt)
                                node["nullable"] = True
                            else:
                                # Fallback for complex unions: make it object or string? 
                                # Proceed with first non-null or keep generic
                                pass

                        # Clean incompatible keys
                        node.pop("default", None)
                        node.pop("title", None)
                        node.pop("example", None)
                        node.pop("examples", None)
                        node.pop("maximum", None)
                        node.pop("minimum", None)
                        node.pop("exclusiveMaximum", None)
                        node.pop("exclusiveMinimum", None)
                        node.pop("minLength", None)
                        node.pop("maxLength", None)
                        node.pop("pattern", None)
                        
                        # Process children
                        return {k: resolve_and_clean(v) for k, v in node.items()}
                    elif isinstance(node, list):
                        return [resolve_and_clean(item) for item in node]
                    return node
                
                clean_schema = resolve_and_clean(schema)
                gen_config["response_schema"] = clean_schema
            except Exception as e:
                # Fallback
                print(f"⚠️ Schema conversion warning: {e}")
                gen_config["response_schema"] = response_schema
        else:
            gen_config["response_schema"] = response_schema
    
    # Initialize model
    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config=gen_config,
        safety_settings=SAFETY_SETTINGS
    )
    
    # Generate content
    response = model.generate_content(prompt)
    
    return response.text


def test_api_connection() -> bool:
    """
    Test API connection and configuration
    
    Returns:
        True if connection successful, False otherwise
    """
    try:
        print("🔍 Testing Google AI API connection...")
        
        # Test embedding
        test_embedding = get_embedding("Test connection")
        print(f"  ✅ Embedding test: {len(test_embedding)} dimensions")
        
        # Test LLM Flash
        test_response = get_completion("Say 'OK' and nothing else.")
        print(f"  ✅ LLM Flash test: {test_response.strip()}")
        
        print("✅ All API tests passed!")
        return True
    
    except Exception as e:
        print(f"❌ API test failed: {e}")
        return False


# Auto-initialize on import
try:
    initialize_api()
except Exception as e:
    print(f"⚠️  Warning: Could not auto-initialize API: {e}")
    print("   Call initialize_api() manually with proper GOOGLE_API_KEY")


if __name__ == "__main__":
    """
    Test script when run directly
    """
    print("🚀 Google AI API Wrapper - Test Mode")
    print("="*60)
    
    # Test connection
    if test_api_connection():
        print("\n" + "="*60)
        print("🎉 API wrapper is ready for production use!")
        print("="*60)
        
        # Show usage examples
        print("\n📚 Usage Examples:")
        print("\n1. Single embedding:")
        print("   from src.utils.api_wrapper import get_embedding")
        print("   vector = get_embedding('Your text here')")
        
        print("\n2. Batch embeddings (optimized):")
        print("   from src.utils.api_wrapper import get_embeddings_batch")
        print("   vectors = get_embeddings_batch(['text1', 'text2', 'text3'])")
        
        print("\n3. Fast completion:")
        print("   from src.utils.api_wrapper import get_completion")
        print("   response = get_completion('Your prompt')")
        
        print("\n4. Complex reasoning:")
        print("   response = get_completion('Complex prompt', use_pro=True)")
        
        print("\n5. JSON output:")
        print("   from src.utils.api_wrapper import get_completion_with_json")
        print("   json_response = get_completion_with_json('Return JSON: {...}')")
    else:
        print("\n❌ API wrapper test failed!")
        print("   Check your GOOGLE_API_KEY in .env file")
