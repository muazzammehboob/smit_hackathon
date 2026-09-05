CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    fare_class fare_class_enum,
    policy_type VARCHAR(50),
    embedding vector(768),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_embedding_hnsw 
ON policy_embeddings USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- RPC for similarity search
CREATE OR REPLACE FUNCTION match_policy_embeddings (
  query_embedding vector(768),
  match_fare_class fare_class_enum,
  match_limit int DEFAULT 3
)
RETURNS TABLE (
  id UUID,
  content TEXT,
  fare_class fare_class_enum,
  policy_type VARCHAR,
  similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    policy_embeddings.id,
    policy_embeddings.content,
    policy_embeddings.fare_class,
    policy_embeddings.policy_type,
    1 - (policy_embeddings.embedding <=> query_embedding) AS similarity
  FROM policy_embeddings
  WHERE policy_embeddings.fare_class = match_fare_class OR policy_embeddings.fare_class IS NULL
  ORDER BY policy_embeddings.embedding <=> query_embedding
  LIMIT match_limit;
END;
$$;

CREATE TABLE IF NOT EXISTS support_draft_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pnr VARCHAR(10) NOT NULL,
    question TEXT NOT NULL,
    draft_response TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops_escalations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reason VARCHAR(100) NOT NULL,
    severity VARCHAR(50) DEFAULT 'HIGH',
    details JSONB DEFAULT '{}',
    ip_address VARCHAR(50),
    status VARCHAR(20) DEFAULT 'OPEN',
    created_at TIMESTAMPTZ DEFAULT NOW()
);