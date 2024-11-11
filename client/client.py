import grpc
from protos import lms_pb2
from protos import lms_pb2_grpc
import PyPDF2  # For PDF handling
import os
import re
from pathlib import Path
import traceback
import colorama
from colorama import Fore, Back, Style
from node_addresses import node_addresses

# Initialize colorama
colorama.init()

# main_server_id = "172.17.48.231:50052" #vanshika
main_server_id = "172.17.48.144:50053" #ranjan

channel = grpc.insecure_channel(main_server_id)
stub = lms_pb2_grpc.LMSStub(channel)
active_sessions = {}
role = None  # Store role globally for menu differentiation


def run_client():
    try:
        print("\n\n\n\n\nWelcome to LMS! \nPlease login to proceed.")
        token = login_module()

        if role == "student":
            get_student_menu(token)
        elif role == "instructor":
            get_instructor_menu(token)

    except grpc.RpcError as error:
        print("RPC Error in run_client: ", error.code(), " - ", error.details())
        traceback.print_exc()
    except Exception as ex:
        print("Error in run_client: ", str(ex))
        traceback.print_exc()

def get_leader(node_address):
    try:
        # print(f"get leader invoked - through node_address: {node_address}")
        channel = grpc.insecure_channel(node_address)
        stub = lms_pb2_grpc.RaftStub(channel)
        response = stub.getLeader(lms_pb2.Empty())
        print(f"Asking {node_address} about leader. Found: {response.leader_ip_address}")
        return response.leader_ip_address
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.UNAVAILABLE:
            print("Error: Service unavailable. Check server status or network connection.") 
        elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            print("Error: Deadline exceeded. Retry the operation.")
        else:
            print("Error:", e.details())
        return None

def get_stub():
    for key,value in node_addresses.items():
        leader_ip = get_leader(value)
        if leader_ip is None:
            continue
        else:
            channel = grpc.insecure_channel(leader_ip)
            break
        
    return lms_pb2_grpc.LMSStub(channel)


def get_student_menu(token):
    while True:
        print("\n=========================================================" +
              "\nMENU: 1: Post Data \t2. Get Data \t3: Logout")
        choice = input(">> ")

        try:
            choice = int(choice)
        except ValueError:
            print("Invalid input. Please enter a number.")
            continue

        if choice == 1:  # Post data
            data_type = input("Enter the type of data to post (e.g., 'assignment', 'query'): ").strip().lower()
            if data_type == 'assignment':
                post_assignment(token)
            elif data_type == 'query':

                query_answer_choice = int(input("Type 1 for recieving answer from INSTRUCTOR or type 2 for recieving answer from LLM TUTORING SERVER: "))

                data_content = input(f"Enter the {data_type} content: ").strip()
                post(token, data_type, data_content, query_answer_choice)

                

        elif choice == 2:  # Get data
            data_type = input("Enter the type of data to retrieve (e.g., 'assignment', 'query','course content'): ").strip().lower()
            optional_data = " "
            get(token, data_type, optional_data)

        elif choice == 3:  # Logout
            logout_success = logout(token)
            if logout_success:
                break

        else:
            print("Invalid choice. Please select a valid option")


def get_instructor_menu(token):
    while True:
        print("\n=========================================================" +
              "\nMENU: 1: Post Data \t2. Get Data \t3: Grade Assignment \t4: Logout")
        choice = input(">> ")

        try:
            choice = int(choice)
        except ValueError:
            print("Invalid input. Please enter a number.")
            continue

        if choice == 1:  # Post data
            data_type = input("Enter the type of data to post (e.g., 'answer query' or 'course content'): ").strip().lower()
            if data_type == "answer query":
                answer_query(get_stub(), token)
            elif data_type == "course content":
                file_path = input("Enter the path to the PDF file (e.g., 'C:/path/to/file.pdf'): ").strip()
                file_type = "course_content"
                post_pdf(token, file_path, file_type)
            else:
                break

        elif choice == 2:  # Get data
            data_type = input("Enter the type of data to retrieve (e.g., 'assignment', 'query'): ").strip().lower()
            optional_data = input("Enter ID for filtering or leave blank: ").strip()
            get(token, data_type, optional_data)

        elif choice == 3:  # Grade assignment
            assignment_id = input("Enter the assignment ID to grade: ").strip()
            grade = input("Enter the grade (e.g., A, B, C, D, F): ").strip().upper()
            grade_assignment(token, assignment_id, grade)

        elif choice == 4:  # Logout
            logout_success = logout(token)
            if logout_success:
                break

        else:
            print("Invalid choice. Please select a valid option")


def login_module():
    global role
    role = input("\nStudent or Instructor - ").strip().lower()
    username = input("Username - ").strip()
    password = input("Password - ").strip()
    login_response = login(role, username, password)

    if login_response.success:
        print(f"Login successful. Welcome, {username}!")
        return str(login_response.token)
    else:
        print("Login failed - Invalid role, username, or password. Please login again!")
        return login_module()


def login(role, username, password):
    login_request = lms_pb2.LoginRequest(role=role, username=username, password=password)
    stub = get_stub()
    try:
        login_response = stub.Login(login_request)

        if login_response.success:
            active_sessions[login_response.token] = login_response.userId
        return login_response

    except grpc.RpcError as error:
        print("RPC Error in login: ", error.code(), " - ", error.details())
    except Exception as ex:
        print("Error in login: ", str(ex))


def logout(token):
    try:
        active_sessions.pop(token)
        print("Logout successful.")
        return True
    except KeyError as err:
        print("Token not found - ", err)
        return False


def post(token, data_type, data, query_answer_choice):
    id = active_sessions[token]
    post_request = lms_pb2.PostRequest(token=id, type=data_type)

    if isinstance(data, str):
        post_request.data = data.encode('utf-8')  # Encode only if it's a string
    elif isinstance(data, bytes):
        post_request.data = data  # Use directly if already bytes
    else:
        print(f"Error: Unsupported data type: {type(data)}")
        return

    try:
        post_response = get_stub().Post(post_request)
        if post_response.success:
            if hasattr(post_response, 'query_id') and post_response.query_id:  # Check if queryId exists and is relevant
                print(Fore.GREEN + f"Data posted successfully: {data_type} !" + Style.RESET_ALL)

                if query_answer_choice == 1: #instructor
                    print("Instructor will answer your query!")
                elif query_answer_choice == 2: #llm 
                    # retrieving query response from llm server
                    get_llm_response(post_response.query_id)


            else:
                print(Fore.GREEN + f"Data posted successfully: {data_type}" + Style.RESET_ALL)
        else:
            print(f"Failed to post {data_type}. Reason: {post_response.message}")
    except grpc.RpcError as error:
        print("RPC Error in post: ", error.code(), " - ", error.details())
        #traceback.print_exc()

    except Exception as ex:
        print("Error in post: ", str(ex))

def post_pdf(token, file_path, file_type):

    filename = os.path.basename(file_path)
    try:
        with open(file_path, 'rb') as file:
            # Reading content for the assignment post
            file_content = file.read()

            # Create a PostRequest for PDF
            id = active_sessions[token]
            post_request = lms_pb2.PostRequest(token=id, filetype=file_type, filename=filename, type='assignment')
            post_request.data = file_content  # Send the binary content

        try:
            post_response = get_stub().Post(post_request)
            if post_response.success:
                print(Fore.GREEN + f"Posted successfully !" + Style.RESET_ALL)
            else:
                print(f"Failed to post PDF file. Reason: {post_response.message}")
        except grpc.RpcError as error:
            print("RPC Error in post_pdf: ", error.code(), " - ", error.details())
        except Exception as ex:
            print("Error in post_pdf: ", str(ex))

    except FileNotFoundError:
        print(f"File not found in post_pdf: {file_path}")
    except IOError as e:
        print(f"Error reading file in post_pdf: {e}")


def post_text_assignment(token, content, filename):
    id = active_sessions[token]
    post_request = lms_pb2.PostRequest(token=id, filetype="assignment_txt", filename=filename, type='assignment', data=content.encode('utf-8'))

    # Encode content to bytes
    post_request.data = content.encode('utf-8')

    try:
        post_response = get_stub().Post(post_request)
        if post_response.success:
            print(Fore.GREEN + "Text Assignment posted successfully." + Style.RESET_ALL)
        else:
            print(f"Failed to post text assignment. Reason: {post_response.message}")
    except grpc.RpcError as error:
        print("RPC Error in post_text_assignment: ", error.code(), " - ", error.details())
    except Exception as ex:
        print("Error in post_text_assignment: ", str(ex))

def post_assignment(token):
    print("Choose a file type to upload:")
    print("1. PDF file")
    print("2. Enter text directly")
    file_choice = input("Choose 1 or 2: ").strip()

    if file_choice == '1':
        file_type = "assignment_pdf"
        file_path = input("Enter the path to the PDF file (e.g., 'C:/path/to/file.pdf'): ").strip()
        post_pdf(token, file_path,file_type)  # Calling modularized function for PDF posting
    elif file_choice == '2':
        assignment_content = input("Enter the content of the assignment: ").strip()
        filename = input("Enter the filename for the assignment (e.g., 'assignment1.txt'): ").strip()
        post_text_assignment(token, assignment_content, filename)  # Calling modularized function for text posting
    else:
        print("Invalid choice. Please choose either 1 or 2.")


def grade_assignment(token, assignment_id, grade):
    grade_request = lms_pb2.GradeRequest(token=token, assignmentId=assignment_id, grade=grade)

    try:
        grade_response = get_stub().Grade(grade_request)
        if grade_response.success:
            print(Fore.GREEN + f"Assignment {assignment_id} graded successfully with {grade}." + Style.RESET_ALL)
        else:
            print(f"Failed to grade assignment {assignment_id}. Reason: {grade_response.message}")
    except grpc.RpcError as error:
        print("RPC Error: ", error.code(), " - ", error.details())
    except Exception as ex:
        print("Error: ", str(ex))


def get(token, data_type, optional_data=None):
    """
    Retrieves data from the server based on the token, type of data, and optional data.
    """
    # Determine role-based data retrieval logic
    if role == "student":
        # For students, the optional_data should be their own ID
        optional_data = active_sessions[token]
        print(f"Retrieving {data_type} for student with ID: {optional_data}")
    elif role == "instructor":
        # Instructors can retrieve all data or filter by student ID
        if optional_data:
            print(f"Retrieving {data_type} for student ID: {optional_data}")
        else:
            print(f"Retrieving all {data_type}")

    # Create the GetRequest with the token, type of data, and optional data if available
    get_request = lms_pb2.GetRequest(token=token, type=data_type, optional_data=optional_data or "")

    try:
        # Call the server's Get method via RPC
        get_response = get_stub().Get(get_request)

        if get_response.success:
            print(f"{data_type.capitalize()} data retrieved successfully.")

            if data_type == "query":
                # Print query-related data
                for item in get_response.data:
                    print(f"\nID: {item.typeId}")
                    print("Data: " + Fore.RED + item.data + Style.RESET_ALL)
                    print("Answer: " + Fore.RED + item.answer + Style.RESET_ALL)

            elif data_type == "assignment":
                # Print assignment data, including grades and content
                for item in get_response.data:
                    grade = item.grade if item.grade else "Not Graded Yet"
                    print(f"\nAssignment ID: {item.assignment_id} \nStudent_ID: {item.typeId}\nGrade: {grade}\n")
                    # Read and display the file content if needed
                    read_file(item.typeId, item.data)

            elif data_type == "course content":
                # Process and save or view the course content (e.g., PDFs)

                    for item in get_response.data:
                        print(f"File Type: {item.file_type}\n")
                        # Call the read_file method with the correct parameters
                        read_file(item.typeId, item.file_path)

        else:
            print(f"Failed to retrieve {data_type}. Reason: {get_response.message}")

    except grpc.RpcError as error:
        print(f"RPC Error in get: {error.code()} - {error.details()}")
    except Exception as ex:
        print(f"Error in get: {str(ex)}")




def save_file(file_path, file_content):
    """
    Saves the retrieved file content to the local system based on the provided file path.
    """
    file_name = os.path.basename(file_path)  # Extract the file name from the path
    with open(file_name, 'wb') as file:
        file.write(file_content)
        print(f"File saved as {file_name}")


def read_file(id, file_path):
    
    normalized_path = Path(file_path)
    
    if os.name != 'nt':
        normalized_path = Path(file_path.replace('\\', '/'))



    try:
        pdf_content=""
        normalized_path=str(normalized_path)
        if normalized_path.endswith(".pdf"):
            with open(normalized_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                print("PDF content:")
                for page in reader.pages:
                    text = page.extract_text()
                    # Replace newlines that are followed by lowercase letters (likely broken words)
                    text = re.sub(r'-\n', '', text)  # Handle hyphenated line breaks
                    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)  # Merge lines but keep paragraph breaks
                    pdf_content += text
                print(Fore.RED + pdf_content + Style.RESET_ALL)
                print("-----------------------------------------------------")
        else:
            with open(normalized_path, 'r', encoding='utf-8') as file:  # Use utf-8 encoding for text files
                content = file.read()
                print(content)
                print("--------------------------------------------")
    except FileNotFoundError:
        print(f"File not found in read_file: {normalized_path}")
        print("Please ensure that the file path is correct.")
    except IOError as e:
        print(f"Error reading file in read_file: {e}")
        print("This could be due to file permissions or a corrupted file.")
    except Exception as ex:
        print(f"An unexpected error occurred in read_file: {ex}")
        print("Please check the file path and try again.")


def get_llm_response(query_id):
    print("Getting response from LLM Tutoring Server...")
    llm_query_request = lms_pb2.LMS_LLMQueryRequest(query_id=query_id)

    try:
        llm_response = get_stub().GetLlmAnswer(llm_query_request)

        if llm_response.success:
            answer = llm_response.response
            print(Fore.RED + f"\n\tAnswer: {answer}"+Style.RESET_ALL)

        else:
            print(f"Some error occurred: {llm_response.response}")

    except grpc.RpcError as error:
        print(f"RPC Error in get_llm_response: {error.code()} - {error.details()}")
    except Exception as ex:
        print(f"Error in get_llm_response: {str(ex)}")

def answer_query(stub, token):
    # Fetch all queries
    get_request = lms_pb2.GetRequest(token=token, type='query')
    response = stub.Get(get_request)

    if not response.success:
        print("Failed to retrieve queries.")
        return

    unanswered_queries = []

    for item in response.data:

        # Check if the query is unanswered (either no 'answered' field or it's set to false)
        if (item.answer=="Not Answered Yet"):
            unanswered_queries.append((item.typeId, item.data))

    # Display unanswered queries
    if not unanswered_queries:
        print("There are no unanswered queries.")
        return

    print("Unanswered Queries:")
    for query_id, query in unanswered_queries:
        print(f"Query ID: {query_id}, Query: {query}")

    # Prompt to answer one of the queries
    query_id = input("Enter the query ID to answer: ")
    answer_data = input("Enter your answer: ")

    answer_request = lms_pb2.AnswerQueryRequest(queryId=query_id, answer=answer_data, token=token)

    try:
        response = stub.AnswerQuery(answer_request)
    except grpc.RpcError as error:
        print(f"RPC Error in get_llm_response: {error.code()} - {error.details()}")
    except Exception as ex:
        print(f"Error in get_llm_response: {str(ex)}")

    if response.success:
        print(Fore.GREEN + f"Answer submitted successfully for Query ID: {query_id}" + Style.RESET_ALL)
    else:
        print(f"Failed to submit answer for Query ID: {query_id} - {response.message}")


if __name__ == '__main__':
    run_client()
