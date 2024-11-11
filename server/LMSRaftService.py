
import grpc
import threading
import random
from concurrent import futures
from protos import lms_pb2
from protos import lms_pb2_grpc
import os




class LMSRaftService(lms_pb2_grpc.RaftServicer):
    def __init__(self, node_id, peers, save_query_func, handle_text_upload, update_data, submit_grade):
        self.node_id = node_id
        # self.peers = {k: v for k, v in peers.items() if k != self.node_id}
        self.peers = peers
        self.current_term = 0
        self.voted_for = None
        self.state = 'Follower'
        self.logs = []
        self.commit_index = 0
        self.next_index = {node:0 for node in peers.keys()}
        
        # self.election_timeout = 15 if self.node_id == 'node1' else 25  # Election timeout in seconds
        if self.node_id == 'node1':
            self.election_timeout = 15
        elif self.node_id == 'node2':
            self.election_timeout = 20
        else:
            self.election_timeout = 25
        self.heartbeat_interval = 15  # Leader heartbeat interval in seconds
        self.election_timer = None
        self.leader_id = None
        self.leader_ip = None
        self.save_query_func = save_query_func #for invoking save_query method of server.py
        self.handle_text_upload = handle_text_upload
        self.update_data = update_data
        self.submit_grade = submit_grade
        self.reset_election_timer()


    def reset_election_timer(self):
        if self.election_timer:
            self.election_timer.cancel()
        #print("reset_election_timer starts")
        self.election_timer = threading.Timer(self.election_timeout, self.start_election)
        self.election_timer.start()
        #print("reset_election_timer stops")


    def start_election(self):
        print("starting election...")
        self.state = 'Candidate'
        self.current_term += 1
        self.voted_for = self.node_id
        votes = 1


        # Request votes from other nodes
        for nodeid, port in self.peers.items():
            print(f"Peer: {nodeid}, {port}")
            if nodeid==self.node_id:
                continue

            request = lms_pb2.VoteRequest(
                term=self.current_term,
                candidate_id=self.node_id,
                last_log_index=len(self.logs) if self.logs else 0,
                last_log_term=self.logs[-1].term if self.logs else 0
            )
            try:
                channel = grpc.insecure_channel(port, options=[('grpc.enable_http_proxy', 0)])
                stub = lms_pb2_grpc.RaftStub(channel)
                response = stub.requestVote(request, timeout=5)
                print(f"Vote received with status from {nodeid} = {response.vote_granted}")
                if response.vote_granted:
                    votes += 1
            except grpc.RpcError as e:
                    if e.code() == grpc.StatusCode.UNAVAILABLE:
                        print("Error: Service unavailable. Check server status or network connection.")
                    elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                        print("Error: Deadline exceeded. Retry the operation.")
                    else:
                        print("Error:", e.details())


        # If majority of votes are received, this node becomes the leader
        if votes > (len(self.peers) // 2):
            print(f"Majority votes received! {self.node_id} becomes leader !")
            self.state = 'Leader'

            self.leader_id = self.node_id
            self.leader_ip = self.peers[self.node_id]
            print(f">>> setting leader id and ip as {self.leader_id} and {self.leader_ip}")


            self.start_heartbeat()
        else:
            # Revert back to follower if election was not won
            self.state = 'Follower'


        self.reset_election_timer()  # Reset election timer after election
        print("start_election stops")

    def get_last_log_info(self):
        if self.logs:
            last_log_term = self.logs[-1].term
            last_log_index = len(self.logs) - 1
            return last_log_index, last_log_term
        else:
            return 0, 0
        
    def is_log_up_to_date(self, lastLogIndex, lastLogTerm):
        print(f"lastLogIndex: {lastLogIndex} lastLogTerm: {lastLogTerm}")
        localLastLogIndex, localLastLogTerm = self.get_last_log_info()
        print(f"localLastLogIndex: {localLastLogIndex} localLastLogTerm: {localLastLogTerm}")
        return not (lastLogTerm < localLastLogTerm or (lastLogTerm == localLastLogTerm and lastLogIndex < localLastLogIndex))
        



    def requestVote(self, request, context):
        print("request vote starts")
        if request.term < self.current_term:
            return lms_pb2.VoteResponse(vote_granted=False)


        # Update term and convert to follower if the term is higher
        if request.term > self.current_term:
            self.current_term = request.term
            self.state = 'Follower'
            self.voted_for = None


        # Grant vote logic
        if (self.voted_for is None or self.voted_for == request.candidate_id) and (self.is_log_up_to_date(request.last_log_index, request.last_log_term)):
            self.voted_for = request.candidate_id
            self.reset_election_timer()  # Reset election timer when vote is granted
            print(f"Vote granted for {self.voted_for}, term {request.term}")
            return lms_pb2.VoteResponse(vote_granted=True)
        else:
            print(f"Vote NOT granted as log index is less than mine - Req.logindex={request.last_log_index}  Self.logindex={self.last_log_term}!")


        print("request vote stops")
        return lms_pb2.VoteResponse(vote_granted=False)




    


    def start_heartbeat(self):
        #print("start_heartbeat starts")
        while self.state == 'Leader':
            for nodeid, port in self.peers.items():
                if nodeid==self.node_id:
                    continue
                self.send_heartbeat(nodeid, port)
            threading.Event().wait(self.heartbeat_interval)  # Heartbeat interval


    def send_heartbeat(self, nodeid, port):
        print(f"Sending heartbeat to {nodeid}, port = {port}")
        # Send heartbeats to followers to maintain leadership
        try:
            channel = grpc.insecure_channel(port, options=[('grpc.enable_http_proxy', 0)])
            stub = lms_pb2_grpc.RaftStub(channel)
            request = lms_pb2.AppendEntriesRequest(term=self.current_term, leader_id=self.node_id, operation="HB")
            stub.appendEntries(request)
        except grpc.RpcError as e:
                if e.code() == grpc.StatusCode.UNAVAILABLE:
                        print("Error: Service unavailable. Check server status or network connection.")
                elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                        print("Error: Deadline exceeded. Retry the operation.")
                else:
                        print("Error in sending heartbeat:", e.details())


    def getLeader(self, request, context):
        temp_leader_id = self.leader_id
        temp_leader_ip = self.leader_ip
        print(f"Client requested leader ID. Current leader is: {temp_leader_id} , {temp_leader_ip}")
        return lms_pb2.LeaderResponse(leader_node_id=temp_leader_id, leader_ip_address=temp_leader_ip)
    



    def appendEntries(self, request, context):
        print(f"\n{request.operation} - Append Entries received from {request.leader_id} - Term:{request.term}")

        # Reset election timer when appendEntries are received (heartbeat)
        if request.term >= self.current_term:
            self.current_term = request.term
            self.state = 'Follower'
            self.leader_id = request.leader_id
            self.leader_ip = self.peers[request.leader_id]
            #print(f">>> Setting leader ID and IP as {self.leader_id} and {self.leader_ip}")

            # Reset the election timer
            self.reset_election_timer()

            if request.operation == "HB":
                return self.appendEntriesReply(self.node_id, request.leader_id, self.current_term, True, len(self.logs))
            
            elif request.operation == "LR-A":
            
                '''
                # Check log consistency
                print(f"prev_log_index:{request.prev_log_index}, ")
                if (request.prev_log_index is not None and
                    request.prev_log_index >= len(self.logs) or
                    (request.prev_log_index > 0 and self.logs[request.prev_log_index - 1].term != request.prevTerm)):
                    print("Log inconsistency detected.")
                    return self.appendEntriesReply(request.leader_id, request.leader_id, self.current_term, False, len(self.logs) - 1)
                '''
            
                # append new entries
                self.logs = self.logs[:request.prev_log_index] + list(request.entries)  # Replace conflicting entries

                print("New entries appended successfully.")
                # print(f">> self.logs = {self.logs}")
                
                return self.appendEntriesReply(self.node_id, request.leader_id, self.current_term, True, len(self.logs) - 1)

            elif request.operation == "LR-C":
                # Update commit index
                # print(f"request.leader_commit: {request.leader_commit}        self.commit_index: {self.commit_index}")
                if request.leader_commit > self.commit_index:
                    self.commit_index = min(request.leader_commit, len(self.logs) - 1)
                # print(f"request.leader_commit: {request.leader_commit}        self.commit_index: {self.commit_index}")

                # write code for saving queries/assignments
                entry = request.entries[0]
                log_entry_req = lms_pb2.LogEntry(token=entry.token, 
                                                type=entry.type, 
                                                data=entry.data,
                                                filename=entry.filename,
                                                filetype=entry.filetype)
                # print(f"log_entry_req: {log_entry_req}")

                if entry.type == "query":
                    self.save_query_func(self, log_entry_req, log_entry_req.token)
                elif entry.type == "assignment" and entry.filetype == "assignment_txt":
                    # print("$$ assignment")
                    project_root = os.path.dirname(__file__)
                    assignment_directory = os.path.join(project_root, 'database', 'assignments')
                    relative_path = self.handle_text_upload(self, log_entry_req, assignment_directory, None)
                    # print(f"$$ relative_path: {relative_path}")
                    if relative_path:
                        # print("yesssssss")
                        self.update_data(self, relative_path=relative_path, id=entry.token, 
                                         directory=assignment_directory, isAssignment=True) 
                    else:
                        print("nooooooo")
                elif entry.grade:
                    print("GRADEEEEEEEEEEEEEEEEEE")
                    print(entry)
                    self.submit_grade(self, request=entry)

                self.commit_index += 1
                print("New entries committed successfully.")
                # print(f">> self.logs = {self.logs}")

                return self.appendEntriesReply(self.node_id, request.leader_id, self.current_term, True, len(self.logs) - 1)

        print("Received an outdated term, rejecting request.")
        return self.appendEntriesReply(self.node_id, request.leader_id, self.current_term, False, len(self.logs) - 1)

    
    def appendEntriesReply(self, from_node, to_node, term, entryAppended, matchIndex):
        response = lms_pb2.AppendEntriesResponse(
            success=entryAppended,
            term=term,
            matchIndex=matchIndex
        )
        print(f"Sending AppendEntriesReply from {from_node} to {to_node}: success={entryAppended}, matchIndex={matchIndex}")
        return response

    
   
    def get_follower_stub(self, follower_node):
        follower_address = self.peers[follower_node]
        channel = grpc.insecure_channel(follower_address)
        return lms_pb2_grpc.RaftStub(channel)










