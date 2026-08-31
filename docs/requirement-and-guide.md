PHẦN A — BỘ KHUNG MÃ NGUỒN CHUNG
Cấu trúc theo file. Đây là skeleton chạy được: dựng được graph, gọi được 3 tool; phần logic miền để
trống bằng TODO cho từng đề tài.
A1. state.py — định nghĩa State
from typing import TypedDict
class AgentState(TypedDict, total=False):
 query: str # câu hỏi gốc
 sub_queries: list[str] # decompose / fusion (nếu có)
 documents: list[dict] # ngữ cảnh truy hồi (vector/graph)
 db_results: list[dict] # kết quả truy vấn database
 web_results: list[dict] # kết quả web search
 grades: dict # điểm relevance / supported
 route: str # quyết định định tuyến
 iteration: int # đếm vòng lặp (chặn loop)
 generation: str # câu trả lời
A2. llm.py — model qua Ollama (nội bộ)
from langchain_ollama import ChatOllama, OllamaEmbeddings
llm = ChatOllama(model="qwen2.5:7b", temperature=0)
embeddings = OllamaEmbeddings(model="bge-m3")
# Yêu cầu: đã chạy `ollama pull qwen2.5:7b` và `ollama pull bge-m3`
A3. ingest.py — nạp corpus vào vector store
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llm import embeddings
def build_index(docs: list[dict], persist="./chroma"):
 splitter = RecursiveCharacterTextSplitter(
 chunk_size=700, chunk_overlap=100)
 chunks, metas = [], []
 for d in docs: # d = {'text':..., 'meta':{}}
 for ch in splitter.split_text(d['text']):
 chunks.append(ch); metas.append(d.get('meta', {}))
 return Chroma.from_texts(chunks, embeddings,
 metadatas=metas, persist_directory=persist,
 collection_name="corpus")
A4. tools.py — 3 công cụ
import sqlite3
from langchain_chroma import Chroma
from langchain_community.tools import DuckDuckGoSearchResults
from llm import embeddings
# 1) RAG retriever
vs = Chroma(collection_name="corpus", embedding_function=embeddings,
 persist_directory="./chroma")
retriever = vs.as_retriever(search_kwargs={"k": 5})
# 2) Database (mở read-only cho an toàn Text-to-SQL)
def run_sql(sql: str, db="app.db") -> list[dict]:
 con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
 con.row_factory = sqlite3.Row
 rows = con.execute(sql).fetchall()
 con.close()
 return [dict(r) for r in rows]
# 3) Web search
web_search = DuckDuckGoSearchResults(output_format="list")
A5. nodes.py — các node dùng chung
from llm import llm
from tools import retriever, run_sql, web_search
def route_question(state):
 # TODO(theo đề tài): phân nhánh knowledge/data/độ-phức-tạp
 return {"route": "knowledge", "iteration": 0}
def retrieve_docs(state):
 docs = retriever.invoke(state["query"])
 return {"documents": [
 {"text": d.page_content, "meta": d.metadata} for d in docs]}
def run_db_query(state):
 sql = state.get("route_sql", "SELECT 1") # TODO: text-to-sql
 return {"db_results": run_sql(sql)}
def run_web_search(state):
 return {"web_results": web_search.invoke(state["query"])}
def grade_or_critique(state):
 # <== ĐIỂM CẮM RAG NÂNG CAO (thay theo từng đề tài, xem Phần B)
 return {"grades": {"verdict": "enough"}}
def generate_answer(state):
 ctx = "\n\n".join(d["text"] for d in state.get("documents", []))
 msg = f"Ngữ cảnh:\n{ctx}\n\nCâu hỏi: {state['query']}\nTrả lời có trích nguồn:"
 return {"generation": llm.invoke(msg).content}
def finalize_answer(state):
 return {"generation": state["generation"]}
A6. graph.py — lắp graph + conditional routing
from langgraph.graph import StateGraph, END
from state import AgentState
from nodes import (route_question, retrieve_docs, run_db_query,
 run_web_search, grade_or_critique, generate_answer, finalize_answer)
MAX_ITER = 2
def pick_branch(state):
 return state["route"] # 'knowledge' | 'data'
def decide_next(state):
 v = state["grades"]["verdict"]
 if v == "enough": return "generate"
 if state.get("iteration", 0) >= MAX_ITER: return "generate"
 return "web" if v == "insufficient" else "rewrite"
g = StateGraph(AgentState)
for name, fn in [("router",route_question),("retrieve",retrieve_docs),
 ("query_db",run_db_query),("web_search",run_web_search),
 ("grade",grade_or_critique),("generate",generate_answer),
 ("finalize",finalize_answer)]:
 g.add_node(name, fn)
g.set_entry_point("router")
g.add_conditional_edges("router", pick_branch,
 {"knowledge":"retrieve", "data":"query_db"})
g.add_edge("retrieve", "grade")
g.add_conditional_edges("grade", decide_next,
 {"generate":"generate", "web":"web_search", "rewrite":"retrieve"})
g.add_edge("web_search", "generate")
g.add_edge("query_db", "generate")
g.add_edge("generate", "finalize")
g.add_edge("finalize", END)
app = g.compile()
Mỗi đề tài ở Phần B chỉ cần thay node grade_or_critique (và đôi khi thêm 1-2 node như build_graph,
decompose, hyde_draft) rồi nối lại cạnh — phần còn lại giữ nguyên.
A7. eval.py — RAGAS + LangFuse (khung)
from ragas import evaluate
from ragas.metrics import (faithfulness, answer_relevancy,
 context_precision, context_recall)
from datasets import Dataset
def run_eval(samples): # {question, answer, contexts, ground_truth}
 ds = Dataset.from_list(samples)
 return evaluate(ds, metrics=[faithfulness, answer_relevancy,
 context_precision, context_recall])
# LangFuse: bọc callback quanh app.invoke(...) để lấy trace mỗi node.

Đề tài 4. Trợ lý phân tích thị trường — RAG-Fusion
Kỹ thuật: RAG-Fusion — nhiều truy vấn con + Reciprocal Rank Fusion.
(1) Node đặc thù
from llm import llm
from tools import retriever
SUBQ = ('Sinh 4 truy vấn con đa dạng cho câu hỏi phân tích, '
 'mỗi dòng một truy vấn, không đánh số:\n{q}')
def gen_subqueries(state):
 r = llm.invoke(SUBQ.format(q=state['query'])).content
 subs = [s.strip('- ').strip() for s in r.splitlines() if s.strip()][:4]
 return {'sub_queries': subs or [state['query']]}
def fusion_retrieve(state, k=60):
 ranklists = [retriever.invoke(q) for q in state['sub_queries']]
 scores = {}
 for docs in ranklists: # Reciprocal Rank Fusion
 for rank, d in enumerate(docs):
 key = d.page_content
 scores[key] = scores.get(key, 0) + 1.0 / (k + rank)
 top = sorted(scores, key=scores.get, reverse=True)[:6]
 return {'documents': [{'text': t, 'meta': {}} for t in top]}
(2) Schema database
CREATE TABLE tickers (ticker TEXT PRIMARY KEY, name TEXT, sector TEXT);
CREATE TABLE prices (
 ticker TEXT, date DATE, open REAL, high REAL, low REAL,
 close REAL, volume INTEGER,
 PRIMARY KEY (ticker, date));
-- nạp từ yfinance (quốc tế) hoặc vnstock (HOSE/HNX)
(3) Test-set mẫu
Câu hỏi Loại Kỳ vọng
Diễn biến và yếu tố ảnh hưởng tới mã X quý
gần nhất?
fusion Gộp tin tức + số liệu; KHÔNG khuyến nghị
mua/bán
So sánh biến động của X và Y trong 6 tháng
qua.
DB+RAG Truy vấn prices + tổng hợp
Ngành Z có tin tức nổi bật nào gần đây? web+RAG Hợp nhất nhiều truy vấn con
(4) Prompt mẫu
Prompt sinh truy vấn con
Sinh 4 truy vấn con đa dạng (góc nhìn khác nhau) cho câu hỏi
phân tích sau, mỗi dòng một truy vấn, không đánh số:
{q}
Prompt tổng hợp phân tích (ràng buộc)
Tổng hợp phân tích DỮ KIỆN từ ngữ cảnh. Trình bày trung lập.
TUYỆT ĐỐI không đưa khuyến nghị mua/bán/nắm giữ.
Ngữ cảnh: {context}\nCâu hỏi: {query}

