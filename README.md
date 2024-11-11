# Distributed Learning Management System with RPC, RAFT & LLM server
###### OPEN THE INNERMOST RPC FOLDER

## Installation
1. Open the terminal (Ensure virtual environment is running)
    1.1 To start virtual env
        1. Creating virtual env
        `python -m venv myenv`

        2. Activating virtual env
        (CMD) `myenv\Scripts\activate`
        (PS) `.\myenv\Scripts\Activate.ps1`

        3. Installing requirements
        `pip install -r requirements.txt`

2.Start LMS Server (will start on port 50051)
`python -m server.server node1 0.0.0.0:50051`
(Node addresses are given in node.addresses.py)

3. Start LLM Server (will start on port 50052)
`python -m server.LLMServer`

4.Start Client
`python -m client.client`

#### User (Student/Instructor) credentials are given in json files:
/server/database/student.json
/server/database/instructor.json


## Command to Generate Proto files
`python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. protos/lms.proto`


