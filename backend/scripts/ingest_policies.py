import asyncio
import os
import glob
from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv

import sys
# Add backend to path so we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.services.embeddings import generate_embedding

def chunk_and_tag(content: str):
    chunks = content.split('\n\n')
    results = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk: continue
        
        # Check if this chunk contains multiple fare classes in bullet points (like change_policy)
        if 'BASIC_ECONOMY' in chunk and 'FLEXIBLE' in chunk and 'PREMIUM_FIRST' in chunk:
            lines = chunk.split('\n')
            general_lines = []
            for line in lines:
                if 'BASIC_ECONOMY' in line:
                    results.append((line, 'BASIC_ECONOMY'))
                elif 'FLEXIBLE' in line:
                    results.append((line, 'FLEXIBLE'))
                elif 'PREMIUM_FIRST' in line:
                    results.append((line, 'PREMIUM_FIRST'))
                else:
                    general_lines.append(line)
            if general_lines:
                results.append(('\n'.join(general_lines).strip(), None))
        else:
            # Determine fare class for the chunk
            if 'BASIC_ECONOMY' in chunk:
                results.append((chunk, 'BASIC_ECONOMY'))
            elif 'FLEXIBLE' in chunk:
                results.append((chunk, 'FLEXIBLE'))
            elif 'PREMIUM_FIRST' in chunk:
                results.append((chunk, 'PREMIUM_FIRST'))
            else:
                results.append((chunk, None))
                
    # Filter out empty chunks that might result from general_lines
    return [(c, f) for c, f in results if c]

async def process_file(filepath: str, supabase: Client):
    filename = os.path.basename(filepath)
    policy_type = filename.replace('.md', '')
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    chunks_with_tags = chunk_and_tag(content)
    
    for chunk, fare_class in chunks_with_tags:
        embedding = await generate_embedding(chunk)
        data = {
            "content": chunk,
            "fare_class": fare_class,
            "policy_type": policy_type,
            "embedding": embedding,
            "metadata": {"source": filename}
        }
        supabase.table("policy_embeddings").insert(data).execute()
        print(f"Ingested chunk for {fare_class} in {policy_type}")

async def main():
    # Force reload of settings to ensure environment variables are present
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
    # Make sure we read GEMINI_API_KEY from environment if not present in settings initially
    if not settings.GEMINI_API_KEY:
        settings.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
        
    supabase_url = settings.SUPABASE_URL
    supabase_key = settings.SUPABASE_SERVICE_ROLE_KEY
    supabase = create_client(supabase_url, supabase_key)
    
    # Optional: Clear existing embeddings to avoid duplicates on re-run
    try:
        supabase.table("policy_embeddings").delete().neq("content", "impossible_value").execute()
        print("Cleared existing policy embeddings.")
    except Exception as e:
        print(f"Could not clear embeddings (might be empty or permission issue): {e}")
    
    policies_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'policies')
    files = glob.glob(f"{policies_dir}/*.md")
    
    for file in files:
        await process_file(file, supabase)
    print("Ingestion complete.")

if __name__ == "__main__":
    asyncio.run(main())
