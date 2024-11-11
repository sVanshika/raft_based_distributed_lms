import grpc
from concurrent import futures
from protos import lms_pb2
from protos import lms_pb2_grpc
import sys

import json
import uuid
import os
from pathlib import Path
import PyPDF2
from .LMSRaftService import LMSRaftService
from node_addresses import node_addresses


llm_channel = grpc.insecure_channel('172.17.48.144:50054')
llm_stub = lms_pb2_grpc.LLMStub(llm_channel)

# Use os.path.join to dynamically construct paths
project_root = os.path.dirname(__file__)
student_path = os.path.join(project_root, 'database', 'student.json')
instructor_path = os.path.join(project_root, 'database', 'instructor.json')
queries_path = os.path.join(project_root, 'database', 'queries.json')
assignments_path = os.path.join(project_root, 'database', 'assignments.json')
assignment_directory = os.path.join(project_root, 'database', 'assignments')
grades_path = os.path.join(project_root, 'database', 'grades.json')
course_directory = os.path.join(project_root, 'database', 'course')
course_json_path = os.path.join(project_root, 'database', 'course.json')

class LMSService(lms_pb2_grpc.LMSServicer):
    def __init__(self, raft_node):
        self.raft_node = raft_node


    def Login(self, request, context):
        try:
            if request.role == "student":
                with open(student_path, 'r') as file:
                    users = json.load(file)
                    user = next((u for u in users if u['username'] == request.username and u['password'] == request.password), None)

                    if user:
                        return lms_pb2.LoginResponse(success=True, userId=user['id'], token=user['id'])
                    else:
                        return lms_pb2.LoginResponse(success=False, userId=None, token=None)

            elif request.role == "instructor":
                with open(instructor_path, 'r') as file:
                    users = json.load(file)
                    user = next((u for u in users if u['username'] == request.username and u['password'] == request.password), None)
                    if user:
                        return lms_pb2.LoginResponse(success=True, userId=user['id'], token=user['id'])
                    else:
                        return lms_pb2.LoginResponse(success=False, userId=None, token=None)

            else:
                return lms_pb2.LoginResponse(success=False, userId=None, token=None)

        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f'Caught exception - Error in login: {str(e)}')
            return lms_pb2.LoginResponse(success=False, userId=None, token=None)

    def Post(self, request, context):
        print("Posting...")
        id = request.token
        #print(request)
        try:
            directory = None  # Initialize directory variable
            if request.type == "assignment":
                relative_path = ""
                
                # print(request)
                # Determine the correct file handling based on the type
                if request.filetype == "assignment_pdf":
                    isAssignment = True
                    directory = assignment_directory
                    relative_path = self.handle_pdf_upload(request, directory, context)
                elif request.filetype == "assignment_txt":
                    isAssignment = True
                    directory = assignment_directory

                    if self.raft_node.state == 'Leader':
                        # append entries requests
                        new_entry = {"token": request.token, 
                                    "type": request.type,
                                    "data": request.data,
                                    "filename": request.filename,
                                    "filetype": request.filetype,
                                    "term": self.raft_node.current_term}
                        votes = self.append_entries_raft(new_entry)
                        # if votes > majority
                        if votes >= (len(self.raft_node.peers) // 2):
                            print(">>>>> majority votes recieved !")
                            # self commit
                            relative_path = self.handle_text_upload(request, directory, context)
                            # send commit requests
                            self.commit_entries_raft(new_entry)
                        else:
                            print(">>>>> majority votes are NOT received")
                            return lms_pb2.PostResponse(success=False, message=f"Majority Votes not received from all followers")
                    
                elif request.filetype == "course_content":
                    isAssignment = False
                    directory = course_directory
                    relative_path = self.handle_pdf_upload(request, directory, context)
                else:
                    return lms_pb2.PostResponse(success=False, message="Unsupported file type")

                # Save the assignment information in the JSON file
                if relative_path:
                    self.update_data(relative_path, id, directory, isAssignment)

                return lms_pb2.PostResponse(success=True,
                                            message=f"{request.filetype.capitalize()} posted successfully")

            elif request.type == "query":
                print(">>>>>> query")
                # print(f">>>>> {self.raft_node},  {self.raft_node.state}")

                new_entry = {}
                new_entry["token"]=request.token
                new_entry["type"]=request.type
                new_entry["data"]=request.data
                new_entry["term"] = self.raft_node.current_term

                # Invoking Raft mechanism for log replication
                if self.raft_node.state == 'Leader':
                    self.raft_node.logs.append(new_entry)
                    # print(f"Appended query entry to leader's log: {new_entry}")

                    # Replicate to followers
                    votes = 0
                    for follower_node in self.raft_node.peers.keys():
                        print(f">>>>> Sending append request to {follower_node}")
                        try:
                            if follower_node != self.raft_node.node_id:
                                follower_stub = self.raft_node.get_follower_stub(follower_node)
                                append_request = lms_pb2.AppendEntriesRequest(
                                    entries=[new_entry],
                                    leader_id=self.raft_node.node_id,
                                    term=self.raft_node.current_term,
                                    prev_log_index=len(self.raft_node.logs) - 1,
                                    prev_log_term=self.raft_node.logs[-1]['term'] if self.raft_node.logs else 0,
                                    leader_commit=self.raft_node.commit_index,
                                    operation="LR-A"
                                )
                                # print(f">>>>> append_request: {append_request}")
                                # print(f">> {append_request.prev_log_index} {append_request.leader_commit}")
                                response = follower_stub.appendEntries(append_request)
                                print(f">>>>> append_entries_response: {response.success} ")
                            
                                if response.success == True:
                                    votes += 1
                                    # print(f">>>>> incrementing votes; votes = {votes}")
                                # else:
                                    # print(">>>>> nothing")
                        except grpc.RpcError as e:
                            if e.code() == grpc.StatusCode.UNAVAILABLE:
                                print("Error: Service unavailable. Check server status or network connection.")
                            elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                                print("Error: Deadline exceeded. Retry the operation.")
                            else:
                                print("Error:", e.details())

                                


                    # Commit the log entry after replication
                    if votes >= (len(self.raft_node.peers) // 2):
                        print(">>>>> majority votes recieved !")
                        
                        self.raft_node.commit_index += 1
                        print(f"Committed query log entry at commit index = {self.raft_node.commit_index}")
                        
                        # Save the query after replication is successful
                        new_query_id = str(self.save_query(request, id))
                        print(f"Query posted successfully on node {self.raft_node.node_id} with ID: {new_query_id}")

                        # Send commit request to all followers
                        print("Sending commit requests to all followers !")
                        for follower_node in self.raft_node.peers.keys():
                            print(f">>>>> {follower_node}")
                            try:
                                if follower_node != self.raft_node.node_id:
                                    follower_stub = self.raft_node.get_follower_stub(follower_node)
                                    commit_request = lms_pb2.AppendEntriesRequest(
                                        entries=[new_entry],
                                        leader_id=self.raft_node.node_id,
                                        term=self.raft_node.current_term,
                                        prev_log_index=len(self.raft_node.logs) - 1,
                                        prev_log_term=self.raft_node.logs[-1]['term'] if self.raft_node.logs else 0,
                                        leader_commit=self.raft_node.commit_index,
                                        operation="LR-C"
                                    )
                                    # print(f">>>>> commit_request: {commit_request}")
                                    # print(f">> {commit_request.prev_log_index} {commit_request.leader_commit}")
                                    response = follower_stub.appendEntries(commit_request)
                                    print(f">>>>> commit_response: {response.success} ")
                            except grpc.RpcError as e:
                                if e.code() == grpc.StatusCode.UNAVAILABLE:
                                    print("Error: Service unavailable. Check server status or network connection.")
                                elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                                    print("Error: Deadline exceeded. Retry the operation.")
                                else:
                                    print("Error:", e.details())
                        


                        return lms_pb2.PostResponse(success=True, message=f"Query posted successfully", query_id=new_query_id)
                    
                    else:
                        print("--- majority votes are NOT received")
                        return lms_pb2.PostResponse(success=False, message=f"Majority Votes not received from all followers")

                else:
                    return lms_pb2.PostResponse(success=False, message="Not the leader")

            else:
                return lms_pb2.PostResponse(success=False, message="Unknown data type")

        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f'Error in post: {str(e)}')
            return lms_pb2.PostResponse(success=False, message="Failed to post data")
        

    def append_entries_raft(self, new_entry):
        votes = 0
        # Invoking Raft mechanism for log replication
        if self.raft_node.state == 'Leader':
            self.raft_node.logs.append(new_entry)
            print(f"Appended query entry to leader's log: {new_entry}")

            # Replicate to followers
            
            for follower_node in self.raft_node.peers.keys():
                print(f">>>>> {follower_node}")
                try:
                    if follower_node != self.raft_node.node_id:
                        follower_stub = self.raft_node.get_follower_stub(follower_node)
                        append_request = lms_pb2.AppendEntriesRequest(
                            entries=[new_entry],
                            leader_id=self.raft_node.node_id,
                            term=self.raft_node.current_term,
                            prev_log_index=len(self.raft_node.logs) - 1,
                            prev_log_term=self.raft_node.logs[-1]['term'] if self.raft_node.logs else 0,
                            leader_commit=self.raft_node.commit_index,
                            operation="LR-A"
                        )
                        # print(f">>>>> append_request: {append_request}")
                        print(f">> {append_request.prev_log_index} {append_request.leader_commit}")
                        response = follower_stub.appendEntries(append_request)
                        # print(f">>>>> append_entries_response: {response.success} ")
                    
                        if response.success == True:
                            votes += 1
                            # print(f">>>>> incrementing votes; votes = {votes}")
                        # else:
                            # print(">>>>> nothing")
                except grpc.RpcError as e:
                    if e.code() == grpc.StatusCode.UNAVAILABLE:
                        print("Error: Service unavailable. Check server status or network connection.")
                    elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                        print("Error: Deadline exceeded. Retry the operation.")
                    else:
                        print("Error:", e.details())

        return votes
    

    def commit_entries_raft(self, new_entry):
        print("Sending commit requests to all followers !")
        for follower_node in self.raft_node.peers.keys():
            print(f">>>>> {follower_node}")
            try:
                if follower_node != self.raft_node.node_id:
                    follower_stub = self.raft_node.get_follower_stub(follower_node)
                    commit_request = lms_pb2.AppendEntriesRequest(
                        entries=[new_entry],
                        leader_id=self.raft_node.node_id,
                        term=self.raft_node.current_term,
                        prev_log_index=len(self.raft_node.logs) - 1,
                        prev_log_term=self.raft_node.logs[-1]['term'] if self.raft_node.logs else 0,
                        leader_commit=self.raft_node.commit_index,
                        operation="LR-C"
                    )
                    print(f">>>>> commit_request: {commit_request}")
                    print(f">> {commit_request.prev_log_index} {commit_request.leader_commit}")
                    response = follower_stub.appendEntries(commit_request)
                    print(f">>>>> commit_response: {response.success} ")
            except grpc.RpcError as e:
                if e.code() == grpc.StatusCode.UNAVAILABLE:
                    print("Error: Service unavailable. Check server status or network connection.")
                elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                    print("Error: Deadline exceeded. Retry the operation.")
                else:
                    print("Error:", e.details())
        





    def handle_pdf_upload(self, request, directory, context):
        file_name = request.filename if request.filename else f'{uuid.uuid4()}.pdf'
        file_path = os.path.join(directory, file_name)
        if directory == assignment_directory:
            relative_path = f"server\\database\\assignments\\{file_name}"
        elif directory == course_directory:
            relative_path = f"server\\database\\course\\{file_name}"

        try:
            with open(file_path, 'wb') as pdf_file:
                pdf_file.write(request.data)  # Assuming `request.data` contains PDF bytes
            return relative_path
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f'Failed to upload PDF: {str(e)}')
            return None

    def handle_text_upload(self, request, directory, context):
        file_name = request.filename if request.filename else f'{uuid.uuid4()}.txt'
        file_path = os.path.join(directory, file_name)
        relative_path = f"server\\database\\assignments\\{file_name}"
        try:
            if isinstance(request.data, str):
                text_data = request.data.encode('utf-8')  # Encode the string to bytes
            elif isinstance(request.data, bytes):
                text_data = request.data  # If it's already bytes, just use it
            else:
                raise ValueError("Unsupported data type for text upload")

                # Save the content to a text file
            with open(str(file_path), 'wb') as text_file:
                text_file.write(text_data)  # Write the bytes to a file
                print(f"Text assignment posted: {file_path}")
            return relative_path
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f'Failed to handle text upload: {str(e)}')
            return None

    def update_data(self, relative_path, id, directory,isAssignment):
        print("******* update_data")
        #file_path = f"server\\database\\course.json" if directory == course_directory else assignments_path
        file_path = course_json_path if directory == course_directory else assignments_path

        if os.path.exists(file_path):
            with open(file_path, 'r') as file:
                data = json.load(file)
        else:
            data = []

        new_entry = {
            "user_id": id,
            "data_path": relative_path
        }
        print(f"******* {new_entry}")
        if (isAssignment == True):
            # Initialize new_assignment_id based on existing assignments for the user
            student_assignments = [a for a in data if a["user_id"] == id]
            if student_assignments:
                try:
                    last_assignment_id = max(
                        [int(a.get("assignment_id", 0)) for a in student_assignments if "assignment_id" in a])
                    new_assignment_id = last_assignment_id + 1
                except ValueError:
                    new_assignment_id = 1
            else:
                new_assignment_id = 1
            new_entry['assignment_id'] = str(new_assignment_id)

        data.append(new_entry)

        print(file_path)
        print()
        with open(file_path, 'w') as file:
            json.dump(data, file, indent=4)

    def save_query(self, request, id):
        print("********* saving query *************")
        # print(request)
        # print(id)
        # Decode request.data from bytes to string if necessary
        query_data = request.data.decode('utf-8') if isinstance(request.data, bytes) else request.data

        # Initialize new_query_id based on existing queries for the user
        if os.path.exists(queries_path):
            with open(queries_path, 'r') as file:
                queries_data = json.load(file)
        else:
            queries_data = []

        student_queries = [q for q in queries_data if q["user_id"] == id]
        if student_queries:
            try:
                last_query_id = max([int(q.get("query_id", 0)) for q in student_queries])
                new_query_id = last_query_id + 1
            except ValueError:
                new_query_id = 1
        else:
            new_query_id = 1

        # Append new query entry
        new_entry = {
            "query_id": new_query_id,
            "user_id": id,
            "data": query_data  # Ensure this is a string
        }
        queries_data.append(new_entry)  # Append the new entry


        # Write back to the JSON file
        with open(queries_path, 'w') as file:
            json.dump(queries_data, file, indent=4)  # Ensure formatting is correct
        return str(new_query_id)

    def Get(self, request, context):
        data_list = []
        try:
            if request.type == "assignment":
                print("Retrieving assignments")
                with open(assignments_path, 'r') as file:
                    assignments = json.load(file)
                    for assignment in assignments:
                        grade = assignment.get('grade', "Not Graded Yet")
                        if request.optional_data == assignment.get('user_id', None) or not request.optional_data:
                   
                            data_list.append(
                                lms_pb2.DataItem(
                                    typeId=assignment.get('user_id', ''),
                                    data=assignment['data_path'],
                                    grade=grade,
                                    assignment_id=assignment.get('assignment_id', '')
                                )
                            )

            elif request.type == "query":
                with open(queries_path, 'r') as file:
                    queries = json.load(file)
                    for query in queries:
                        if request.optional_data == query.get('user_id', None) or not request.optional_data:
                            #print(query.get('query_id'))
                            #print(query.get('user_id'))
                            data_list.append(
                                lms_pb2.DataItem(typeId=str(query.get('query_id', '')), data=query['data'],answer=query.get('answer',"Not Answered Yet")))

                        elif not request.optional_data:
                            data_list.append(
                                lms_pb2.DataItem(typeId=str(query.get('user_id', '')), data=query['data'],answer=query.get('answer',''))
                            )

            elif request.type == "student":
                with open(student_path, 'r') as file:
                    students = json.load(file)
                    for student in students:
                        if request.optional_data == student.get('user_id', None) or not request.optional_data:
                            data_list.append(
                                lms_pb2.DataItem(typeId=student.get('user_id', ''), data=student['username'])
                            )
            elif request.type == "course content":
                try:
                    # Construct the path for the course.json file
                    print("Retrieving course content")
                    course_json_path = os.path.join(project_root, 'database', 'course.json')


                    # Check if the course.json file exists
                    if not os.path.exists(course_json_path):
                        print("Course data file not found.")
                        return lms_pb2.GetResponse(success=False, message="Course data file not found", data=[])

                    with open(course_json_path, 'r') as file:
                        course_data = json.load(file)
                        print("Course Data Loaded: ", course_data)  # Debugging line

                        # Iterate over the course data to retrieve the course content
                        for content in course_data:
                            # Read the file content (e.g., PDF)
                            file_path = content.get('data_path', None)

                            absolute_file_path = os.path.join(project_root, *file_path.split('\\')[1:])


                            if os.path.exists(absolute_file_path):
                                file_content = self.read_file(
                                    absolute_file_path)  # Assuming you have a method to read the file
                                file_bytes = b''
                                print(f"Successfully read file content from: {absolute_file_path}")

                                # Add the file content and its metadata to the response
                                data_list.append(
                                    lms_pb2.DataItem(
                                        typeId=content.get('user_id', ''),

                                        file_content=file_bytes,
                                         # Include the file path
                                        file_path=absolute_file_path,
                                        file_type='pdf' if file_path.endswith('.pdf') else 'txt',
                                        data=file_content , # Include the file content
                                        timestamp = content.get('timestamp', '')


                                )
                                )


                            else:
                                context.set_code(grpc.StatusCode.NOT_FOUND)
                                context.set_details("Course content file not found")

                                return lms_pb2.GetResponse(success=False, message="Course content file not found",
                                                           data=[])
                    print("Course Content retrived")

                    return lms_pb2.GetResponse(success=True, data=data_list,
                                               message="Course content retrieved successfully")

                except Exception as e:
                    context.set_code(grpc.StatusCode.INTERNAL)
                    context.set_details(f'Error in get: {str(e)}')
                    return lms_pb2.GetResponse(success=False, message=f"Error: {str(e)}", data=[])




            print("Data retrieved successfully")
            return lms_pb2.GetResponse(success=True, data=data_list, message="Data retrieved successfully")

        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f'Error: {str(e)}')
            return lms_pb2.GetResponse(success=False, message=f'Error: {str(e)}', data=[])

    def Grade(self, request, context):
        print(">>> Grading...")
        print(request)
        try:
            if self.raft_node.state == 'Leader':
                # append entries requests
                new_entry = {"token": request.token, 
                            "assignmentId": request.assignmentId,
                            "grade": request.grade,
                            "term": self.raft_node.current_term}
                votes = self.append_entries_raft(new_entry)
                # if votes > majority
                if votes >= (len(self.raft_node.peers) // 2):
                    print(">>>>> majority votes recieved !")
                    
                    ''' self commit '''
                    self.submit_grade(request=request)
                    ''' send commit requests '''
                    self.commit_entries_raft(new_entry)
                    return lms_pb2.GradeResponse(success=True, message="Grade submitted successfully.")
                else:
                    print("--- majority votes are NOT received")
                    return lms_pb2.GradeResponse(success=False, message="Grade NOT submitted .")
                
                        
            

        except json.JSONDecodeError as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f'JSON Decode Error: {str(e)}')
            return lms_pb2.GradeResponse(success=False, message="Failed to decode assignments data.")
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f'Error grading assignment: {str(e)}')
            return lms_pb2.GradeResponse(success=False, message="Failed to grade assignment.")

    def submit_grade(self, request):
            assignment_id = request.assignmentId
            grade = request.grade

            # Load existing assignments
            with open(assignments_path, 'r') as file:
                assignments_data = json.load(file)

            # Find the assignment and update the grade
            for assignment in assignments_data:
                if str(assignment.get("assignment_id", "")) == assignment_id:
                    assignment["grade"] = grade  # Add the grade field
                    print(f"Grade for assignment ID {assignment_id} set to {grade}.")
                    break
            else:
                #context.set_code(grpc.StatusCode.NOT_FOUND)
                #context.set_details(f'Assignment ID {assignment_id} not found.')
                return lms_pb2.GradeResponse(success=False, message="Assignment not found.")

            # Save updated assignments data
            with open(assignments_path, 'w') as file:
                json.dump(assignments_data, file, indent=4)

            return lms_pb2.GradeResponse(success=True, message="Grade submitted successfully.")

    def read_file(self, file_path):

        try:
            if file_path.endswith(".pdf"):
                pdf_content = ""
                with open(file_path, 'rb') as file:
                    reader = PyPDF2.PdfReader(file)

                    #print("PDF content:")
                    for page in reader.pages:
                        pdf_content += page.extract_text() + "\n"  # Extract text and append it


            else:
                with open(file_path, 'r', encoding='utf-8') as file:  # Use utf-8 encoding for text files
                    content = file.read()
                    #print(content)
                    #print("-----------------------")
                    return content.strip()
        except FileNotFoundError:
            print(f"File not found: {file_path}")
            print("Please ensure that the file path is correct.")
        except IOError as e:
            print(f"Error reading file: {e}")
            print("This could be due to file permissions or a corrupted file.")
        except Exception as ex:
            print(f"An unexpected error occurred: {ex}")
            print("Please check the file path and try again.")

    def AnswerQuery(self, request, context):
        print(">>> answer query")
        print(request)
        try:
            query_id = request.queryId
            answer = request.answer
            user_id = request.token# Assuming the answer is sent in the request

            # Load existing queries
            if os.path.exists(queries_path):
                with open(queries_path, 'r') as file:
                    queries_data = json.load(file)
            else:
                queries_data = []

            # Find the query and update the answer
            for query in queries_data:
                if str(query.get("query_id", "")) == query_id:
                    '''if "answered" in query and query["answered"]:

                        context.set_details(f'ERROR: Query {query_id} has already been answered.')
                        return lms_pb2.AnswerQueryResponse(success=False, message="Query already answered")'''

                    # Update the query with the answer and mark it as answered
                    query["answer"] = answer
                    query["answered"] = True
                    query["answered_by"] = user_id
                    break
            else:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f'Query ID {query_id} not found.')
                return lms_pb2.AnswerQueryResponse(success=False, message="Query not found.")

            # Save updated queries data
            with open(queries_path, 'w') as file:
                json.dump(queries_data, file, indent=4)

            return lms_pb2.AnswerQueryResponse(success=True, message="Query answered successfully.")

        except json.JSONDecodeError as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f'JSON Decode Error: {str(e)}')
            return lms_pb2.AnswerQueryResponse(success=False, message="Failed to decode queries data.")
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f'Error answering query: {str(e)}')
            return lms_pb2.AnswerQueryResponse(success=False, message="Failed to answer query.")


    def GetLlmAnswer(self, request, context):
        print("Requesting LLM for answer:")
        try:
            req_query_id = request.query_id
            question = ''
    
            # loading existing queries
            with open(queries_path, 'r') as file:
                all_queries = json.load(file)
            
            # finding the query with 'id' and updating answer
            for query in all_queries:
                if req_query_id == str(query.get("query_id", 0)):
                    question = query.get("data", '')

                    # invocation of llm server to get answer
                    print(f"Question: {question}")
                    llm_request = lms_pb2.LLMQueryRequest(question=question)
                    llm_answer = llm_stub.GetLLMAnswerResponse(llm_request)
                    print(f"Answer: {llm_answer.answer}")
                    query['answer'] = llm_answer.answer

                    break

            else:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f'Query ID {req_query_id} not found.')
                return lms_pb2.LMS_LLMResponse(success=False, response="Failed to get response from LLM Tutoring Server.")

            # Save updated assignments data
            with open(queries_path, 'w') as file:
                json.dump(all_queries, file, indent=4)
            
            return lms_pb2.LMS_LLMResponse(success=True, response=llm_answer.answer)
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f'LMS Server Error: {str(e)}')
            print(f"Error in getllmanswer: "+ str(e))
            return lms_pb2.LMS_LLMResponse(success=False, response="Failed to get response from LLM Tutoring Server.")
       



def serve(node_name, bind_address):
    raft_service = LMSRaftService(node_name, node_addresses, 
                                  LMSService.save_query, LMSService.handle_text_upload,
                                  LMSService.update_data, LMSService.submit_grade)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    lms_pb2_grpc.add_LMSServicer_to_server(LMSService(raft_service), server)

    lms_pb2_grpc.add_RaftServicer_to_server(raft_service, server)

    server.add_insecure_port(bind_address)
    server.start()

    print("\n\nLMS Server with Raft running on", node_addresses[node_name])
    
    server.wait_for_termination()


if __name__ == '__main__':
    node_name = sys.argv[1]
    bind_address = sys.argv[2]
    serve(node_name, bind_address)
