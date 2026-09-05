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

def chunk_text(text: str, chunk_size=1600, overlap=200):
    """
    Very basic character-based chunking that tries to roughly approximate
    400 tokens (assuming ~4 chars per token).
    """
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        
        # Adjust end to nearest double newline if possible
        if end < len(text):
            nearest_break = text.rfind("\n\n", start, end)
            if nearest_break > start + overlap:
                end = nearest_break + 2

        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else len(text)
        
    return [c for c in chunks if c]

async def process_file(filepath: str, supabase: Client):
    filename = os.path.basename(filepath)
    policy_type = filename.replace('.md', '')
    
    with open(filepath, 'r') as f:
        content = f.read()

    # Heuristic: Find fare class mentions and assign chunks
    # For a robust approach, we parse the sections by fare class
    fare_classes = ['BASIC_ECONOMY', 'FLEXIBLE', 'PREMIUM_FIRST']
    
    # Split by fare class headers roughly
    for fc in fare_classes:
        if fc in content:
            # We will just embed chunks and assign the fare class based on the section
            # For this hackathon implementation, we'll embed the specific fare class section.
            fc_index = content.find(fc)
            if fc_index != -1:
                # Find end of this section (next fare class or end of file)
                next_fc_index = len(content)
                for other_fc in fare_classes:
                    if other_fc != fc:
                        idx = content.find(other_fc, fc_index + 1)
                        if idx != -1 and idx < next_fc_index:
                            next_fc_index = idx
                
                section_text = content[max(0, fc_index - 50):next_fc_index].strip()
                chunks = chunk_text(section_text)
                
                for chunk in chunks:
                    embedding = await generate_embedding(chunk)
                    data = {
                        "content": chunk,
                        "fare_class": fc,
                        "policy_type": policy_type,
                        "embedding": embedding,
                        "metadata": {"source": filename}
                    }
                    supabase.table("policy_embeddings").insert(data).execute()
                    print(f"Ingested chunk for {fc} in {policy_type}")

async def main():
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
    supabase_url = settings.SUPABASE_URL
    supabase_key = settings.SUPABASE_SERVICE_ROLE_KEY
    supabase = create_client(supabase_url, supabase_key)
    
    policies_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'policies')
    files = glob.glob(f"{policies_dir}/*.md")
    
    for file in files:
        await process_file(file, supabase)
    print("Ingestion complete.")

if __name__ == "__main__":
    asyncio.run(main())
