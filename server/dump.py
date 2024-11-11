import grpc
import threading
import random
from concurrent import futures
from protos import lms_pb2
from protos import lms_pb2_grpc
import traceback

class LMSRaftService(lms_pb2_grpc.RaftServicer):
    def __init__(self, node_id, peers):
        self.node_id = node_id
        # self.peers = {k: v for k, v in peers.items() if k != self.node_id}
        self.peers = peers
        self.current_term = 0
        self.voted_for = None
        self.state = 'Follower'
        self.logs = []
        self.election_timeout = 10  # Election timeout in seconds
        self.heartbeat_interval = 5  # Leader heartbeat interval in seconds
        self.election_timer = None
        self.leader_id = None  # Store the current leader ID
        self.leader_ip = None # Store the current leader's ip address
        self.reset_election_timer()

    def reset_election_timer(self):
        if self.election_timer:
            self.election_timer.cancel()
        # print("reset_election_timer starts")
        self.election_timer = threading.Timer(self.election_timeout, self.start_election)
        self.election_timer.start()
        print("election timer started")

    def start_election(self):
        print("\nStarting election...")
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
                last_log_index=len(self.logs), 
                last_log_term=self.logs[-1].term if self.logs else 0
            )
            try:
                channel = grpc.insecure_channel(port, options=[('grpc.enable_http_proxy', 0)])
                stub = lms_pb2_grpc.RaftStub(channel)
                print(f">> Sending vote request to {nodeid} for term {request.term}")
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
            self.leader_id = self.node_id  # Update leader ID
            self.leader_ip = self.peers[self.leader_id]
            print(f"Set leader id - {self.leader_id} and ip address - {self.leader_ip}")
            self.start_heartbeat()
        else:
            # Revert back to follower if election was not won
            self.state = 'Follower'
        
        self.reset_election_timer()  # Reset election timer after election
        print("start_election stops")

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
        if self.voted_for is None or self.voted_for == request.candidate_id:
            self.voted_for = request.candidate_id
            self.reset_election_timer()  # Reset election timer when vote is granted
            print(f"Vote granted for {self.voted_for}, term {request.term}")
            return lms_pb2.VoteResponse(vote_granted=True)
        
        print("request vote stops")
        return lms_pb2.VoteResponse(vote_granted=False)

    def appendEntries(self, request, context):
        print(f"Append Entries recieved from {request.leader_id}")
        try:
            # Reset election timer when appendEntries are received (heartbeat)
            if request.term >= self.current_term:
                self.current_term = request.term
                self.state = 'Follower'
                self.leader_id = request.leader_id
                self.leader_ip = self.peers[self.leader_ip]
                print(f"Set leader id - {self.leader_id} and ip address - {self.leader_ip}")
                self.reset_election_timer()
                return lms_pb2.AppendEntriesResponse(success=True)
            return lms_pb2.AppendEntriesResponse(success=False)
        except Exception as e:
            print(f"Error in appendEntries: {e}")
            context.set_details(f"Exception calling application: {str(e)}")
            context.set_code(grpc.StatusCode.UNKNOWN)
            return None

    def start_heartbeat(self):
        # print("start_heartbeat starts")
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
            request = lms_pb2.AppendEntriesRequest(term=self.current_term, leader_id=self.node_id)
            stub.appendEntries(request)
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.UNAVAILABLE:
                print("Error: Service unavailable. Check server status or network connection.")
            elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                print("Error: Deadline exceeded. Retry the operation.")
            else:
                print("Error while sending heartbeat:", e.details())
                traceback.print_exc()


    def getLeader(self, request, context):
        """Return the current leader."""
        print(f"Client requested leader ID. Current leader is: {self.leader_id} , {self.leader_ip}")
        return lms_pb2.LeaderResponse(leader_node_id=self.leader_id, leader_ip_address=self.leader_ip)
    








    
