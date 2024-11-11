import grpc
from concurrent import futures
from protos import lms_pb2
from protos import lms_pb2_grpc
from transformers import pipeline
from transformers import GPT2LMHeadModel, GPT2Tokenizer
from pathlib import Path
import colorama
from colorama import Fore, Back, Style

# Initialize colorama
colorama.init()



# Loading the DistilBERT model which is fine-tuned on Stanford Question Answering Dataset
qa_pipeline = pipeline("question-answering", model="distilbert-base-uncased-distilled-squad")
llm_context = Path('server/database/context.txt').read_text()

class LLMService(lms_pb2_grpc.LLMServicer):
    # Function to generate answer from 
    def GetLLMAnswerResponse(self, request, context):
        try:
            

            print(f"Req: {request}")
            query = request.question

            print(f"LLM Question: {query}")
         
            result = qa_pipeline(question=query, context=llm_context)
            print(f"Result: {result}")
            answer = result['answer']
            print(f"Answer: {answer}")
            return lms_pb2.LLMResponse(answer=answer)
        except Exception as e:
            print(f"LLM Error: {e}")


def serve_llm():
    llm_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    lms_pb2_grpc.add_LLMServicer_to_server(LLMService(), llm_server)
    llm_server.add_insecure_port('0.0.0.0:50054')
    llm_server.start()
    print("\nLLM Server running on port 50054!")
    llm_server.wait_for_termination()

if __name__ == '__main__':
    serve_llm()

