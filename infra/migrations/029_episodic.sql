-- Bài 29: Episodic memory
-- No Postgres tables needed — episodic_memory lives in Qdrant.
-- The collection is auto-created by memory/episodic.py on first store/retrieve.
-- Collection name: episodic_memory
-- Vector size: 1024 (bge-m3), Distance: COSINE
-- Payload fields: conversation_id, user_id, first_question, summary, conclusion, feedback, created_at
-- Expiry: 90 days (enforced at retrieval time via created_at filter)
SELECT 1; -- no-op placeholder so run_migration() can process this file
