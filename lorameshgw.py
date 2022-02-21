import logging
import logging.handlers
import os
import signal
import time
import subprocess
import threading   
import sys
import json
import socket
from datetime import datetime

# For reading tabular data
import pandas as pd
from io import StringIO
from contextlib import redirect_stdout

# Meshtastic device API
import meshtastic
from pubsub import pub

# REST API library
from flask import Flask
from flask import jsonify
from flask import request

# Settings
# Retrieve command comm configuration
from settings import localip
from settings import portrxmesh
from settings import portrxmsg
from settings import portserver

app = Flask(__name__)

# Global variable declaration
backLogger         = False    # Macro for logger
secureInSecure     = False    # REST API Server
firstNodesData     = False    # First poll for nodes data
ttyMeshNAvail      = False    # Checking TTY creation flag
meshSerInterface   = None     # Meshtastic serial interface 
meshNodes          = None     # Meshtastic nodes
pollNodesCnt       = 0        # Polling counter for nodes info
textMsgTotRec      = 0        # Received text message total record
sendTcpDataType    = 0        # Type of the TCP data that needs to be send to node red
tcpIpAddr          = ''       # IP address for node red TCP server
tcpPortNoMesh      = 0        # Port number for node red TCP server - RX mesh nodes data
tcpPortNoRxMsg     = 0        # Port number for node red TCP server - RX text message
tcpServPortNo      = 0        # TCP server port no.
connSerialMeshCnt  = 0        # Attempt to initialize MESH serial hardware counter

# Mesh node list data
meshNodeListData=[
    {
        'No' : 'NA',
        'User' : 'NA',
        'AKA' : 'NA',
        'ID' : 'NA',
        'Latitude' : 'NA',
        'Longitude' : 'NA',
        'Altitude' : 'NA',
        'Battery' : 'NA',
        'SNR' : 'NA',
        'LastHeard' : 'NA',
        'Since' : 'NA',
    }
]

# Initialize back mesh node list data
meshNodeListData = []

# Get the TCP server IP address and port number
tcpIpAddr = localip
tcpPortNoMesh = int(portrxmesh)
tcpPortNoRxMsg = int(portrxmsg)
tcpServPortNo = int(portserver)

# Check for macro arguments
if (len(sys.argv) > 1):
    for x in sys.argv:
        # Optional macro if we want to enable text file log
        if x == "LOGGER":
            backLogger = True
        # Optional macro if we want to enable https
        elif x == "SECURE":
            secureInSecure = True

# Setup log file 
if backLogger == True:
    path = os.path.dirname(os.path.abspath(__file__))
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logfile = logging.handlers.TimedRotatingFileHandler('/tmp/loraMesgGW.log', when="midnight", backupCount=3)
    formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')
    logfile.setFormatter(formatter)
    logger.addHandler(logfile)

# Doing string manipulations
def mid(s, offset, amount):
    return s[offset-1:offset+amount-1]

# Handle Cross-Origin (CORS) problem upon client request
@app.after_request
def add_headers(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE')

    return response

# Get current mesh nodes information
# Example command to send:
# https://voip.scs.my:9000/meshnodesinfo
@app.route('/meshnodesinfo', methods=['GET'])
def getMeshNodesInfoDb():
    return jsonify({'MeshNodesInfo' : meshNodeListData})

# Call back function to receive messages from mesh nodes
def onReceive(packet, interface): # called when a packet arrives
    global backLogger
    global meshNodeListData
    global tcpIpAddr 
    global tcpPortNoRxMsg

    rxData = ''
    txtMsgPayLoad = ''
    sendMsgPayload = ''
    msgTimeStamp = ''
    senderId = ''
    nodeUsrNme = ''
    nodeAka = ''
    lenRxData = 0
    curlyOpenCnt = 0
    colonCnt = 0
    apostCnt = 0
    curlyOpenFnd = False
    retrTextMsg = False
    semiColonFnd = False
    colonFnd = False
    apostFnd = False
    retrTimeStmp = False
    retrSenderId = False
    
    rxData = str(f"{packet}")
    #rxData = ''
    
    # Write to logger - For debugging
    if backLogger == True:
        logger.info("DEBUG_CALL_BACK_RX_MSG: RX message: %s" % (rxData))
        logger.info(" ")
        logger.info("####################################################################")
        logger.info(" ")
    # Print statement
    else:
        print ("DEBUG_CALL_BACK_RX_MSG: RX message: %s" % (rxData))
        print (" ")
        print ("####################################################################")
        print (" ")

    # Check the received message contents
    # Text message from mesh nodes exist
    if 'TEXT_MESSAGE_APP' in rxData:
        lenRxData = len(rxData)

        # Go through the data
        for a in range(0, (lenRxData + 1)):
            oneChar = mid(rxData, a, 1)
            # Start count '{' character
            if oneChar == '{' and curlyOpenFnd == False:
                curlyOpenCnt += 1
                # Finish counting, indicate the next data are the message payload
                # Set flag to indicate character '{' searching ends
                if curlyOpenCnt == 3:
                    curlyOpenFnd = True

            # Check for '"' to start retrieve text message payload
            elif curlyOpenFnd == True and retrTextMsg == False:
                # Found the first '"' character, indicate the next data are the text message
                if oneChar == chr(34):
                    # Set flag to start retrieve text message payload
                    retrTextMsg = True

            # Start retrieve text message payload
            elif retrTextMsg == True and colonFnd == False:
                # Append text message payload
                if oneChar != chr(34):
                    txtMsgPayLoad += oneChar

                # Found end of text message payload, indicate by character '"'
                # Set flag to check ':' character
                else:
                    colonFnd = True

            # Check for ':' character to start retrieve text message time stamp 
            elif colonFnd == True and retrTimeStmp == False:
                # Count ':' character occurance
                if oneChar == chr(58):
                    colonCnt += 1

                    # 2 ':' character occurance indicate we can start retrieve text message time stamp
                    # Set flag to start retrieve text message time stamp
                    if colonCnt == 2:
                        retrTimeStmp = True

            # Start retrieve text message time stamp
            elif retrTimeStmp == True and retrSenderId == False:
                # Found '\n' character indicate text message payload retrieval was completed
                # Set flag to retrieve sender ID 
                if oneChar == '\n':
                    retrSenderId = True
                    
                # Append text message time stamp
                elif oneChar != ' ':
                    msgTimeStamp += oneChar

            # Check for ''' to start retrieve sender ID
            elif retrSenderId == True and apostFnd == False:
                # Count ''' character occurance
                if oneChar == chr(39):
                    apostCnt += 1

                    # 3 ''' character occurance indicate we can start retrieve sender ID
                    # Set flag to start retrieve sender ID
                    if apostCnt == 3:
                        apostFnd = True

            # Start retrieve sender ID
            elif apostFnd == True:
                # Only append character without ''' and '!' character
                if oneChar != chr(39) and oneChar != chr(33):
                    senderId += oneChar

                # Found last ''' character which indicate end of the sender ID
                elif oneChar == chr(39):
                    break

        # Get the user name and A.K.A from mesh nodes list of data
        # Update python data dictionary with retrieve nodes info
        try:
            # Check the nodes ID whether its already exist or not
            # Update the existing data, throw error if record is not exist, consider its a new data
            nodeDB = [ nodeDBB for nodeDBB in meshNodeListData if (nodeDBB['ID'] == senderId) ]

            # Convert time stamp seconds to date and time
            msgTimeStamp = datetime.fromtimestamp(int(msgTimeStamp)).strftime("%A, %B %d, %Y %I:%M:%S")

            # Get the user name and a.k.a from mesh nodes list of data
            nodeUsrNme = nodeDB[0]['User']
            nodeAka = nodeDB[0]['AKA']

            # Write to logger - For debugging
            if backLogger == True:
                logger.info("DEBUG_CALL_BACK_RX_MSG: TEXT message payload: [%s], Date and Time: [%s], Sender ID: [%s], User Name: [%s], A.K.A: [%s]" % \
                            (txtMsgPayLoad, msgTimeStamp, senderId, nodeUsrNme, nodeAka))
                logger.info(" ")
                logger.info("####################################################################")
                logger.info(" ")
            # Print statement
            else:
                print ("DEBUG_CALL_BACK_RX_MSG: TEXT message payload: [%s], Date and Time: [%s], Sender ID: [%s], User Name: [%s], A.K.A: [%s]" % \
                            (txtMsgPayLoad, msgTimeStamp, senderId, nodeUsrNme, nodeAka))
                print (" ")
                print ("####################################################################")
                print (" ")

            # Construct message receive to send
            sendMsgPayload = '!' + nodeUsrNme + '!' + nodeAka + '!' + senderId + '!' + txtMsgPayLoad + '!' + msgTimeStamp
            try:
                # Socket initialization                
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((tcpIpAddr, tcpPortNoRxMsg))

                # Write to logger - For debugging
                if backLogger == True:
                    logger.info("DEBUG_THD_TCP_CLIENT: Sending mesh received text message data: [%s}" % (sendMsgPayload))
                    logger.info(" ")
                    logger.info("####################################################################")
                    logger.info(" ")
                # Print statement
                else:
                    print ("DEBUG_THD_TCP_CLIENT: Sending mesh received text message data: [%s]" % (sendMsgPayload))
                    print (" ")
                    print ("####################################################################")
                    print (" ")

                # Send the mesh nodes data to node red TCP server
                sock.send(bytearray(sendMsgPayload.encode()))
                sendMsgPayload = ''
                # Close socket
                sock.close()
                
            # Error during sending the data
            except:
                # Write to logger - For debugging
                if backLogger == True:
                    logger.info("DEBUG_CALL_BACK_RX_MSG: Send message payload FAILED!")
                    logger.info(" ")
                    logger.info("####################################################################")
                    logger.info(" ")
                # Print statement
                else:
                    print ("DEBUG_CALL_BACK_RX_MSG: Send message payload FAILED!")
                    print (" ")
                    print ("####################################################################")
                    print (" ")

        # New cell number
        except:
            # Write to logger - For debugging
            if backLogger == True:
                logger.info("DEBUG_CALL_BACK_RX_MSG: Sender ID NOT FOUND!")
                logger.info(" ")
                logger.info("####################################################################")
                logger.info(" ")
            # Print statement
            else:
                print ("DEBUG_CALL_BACK_RX_MSG: Sender ID NOT FOUND!")
                print (" ")
                print ("####################################################################")
                print (" ") 
        
# Call back function when connected to the radio
def onConnection(interface, topic=pub.AUTO_TOPIC): # called when we (re)connect to the radio
    global meshSerInterface

    # Write to logger - For debugging
    if backLogger == True:
        logger.info("DEBUG_CALL_BACK_CONNECTED: Connected to MESH network")
        logger.info("####################################################################")
        logger.info(" ")
    # Print statement
    else:
        print ("DEBUG_CALL_BACK_RX_MSG: Connected to MESH network")
        print ("####################################################################")
        print (" ")
        
    # defaults to broadcast, specify a destination ID if you wish
    meshSerInterface.sendText("hello mesh world")

# Thread to receive text message input from Node Red
def thread_tcpServer_NodeRed (name):
    global backLogger
    global tcpIpAddr
    global tcpServPortNo
    global meshSerInterface

    clientStatus = False
    rxTextMsgData = ''

    # Create a TCP/IP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # Get the old state of the SO_REUSEADDR option
    old_state = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR )

    # Write to logger
    if backLogger == True:
        logger.info("DEBUG_THD_TCP_SERVER: OLD sock state: %s" % (old_state))
        logger.info("####################################################################")
        logger.info(" ")
    # Print statement
    else:
        print ("DEBUG_THD_TCP_SERVER: OLD sock state: %s" % (old_state))
        print ("####################################################################")
        print (" ")

    # Enable the SO_REUSEADDR option
    sock.setsockopt( socket.SOL_SOCKET, socket.SO_REUSEADDR, 1 )
    new_state = sock.getsockopt( socket.SOL_SOCKET, socket.SO_REUSEADDR )

    # Write to logger
    if backLogger == True:
        logger.info("DEBUG_THD_TCP_SERVER: NEW sock state: %s" % (new_state))
        logger.info("####################################################################")
        logger.info(" ")
    # Print statement
    else:
        print ("DEBUG_THD_TCP_SERVER: NEW sock state: %s" % (old_state))
        print ("####################################################################")
        print (" ")

    # Bind the socket to the port
    server_address = (tcpIpAddr, tcpServPortNo)

    # Write to logger
    if backLogger == True:
        # log events, when daemon run in background
        logger.info("DEBUG_THD_TCP_SERVER: Messaging TCP Server starting up on %s port %s" % (server_address))
        logger.info("####################################################################")
        logger.info(" ")
    # Print statement
    else:
        print ("DEBUG_THD_TCP_SERVER: Radar track TCP Server starting up on %s port %s" % (server_address))
        print ("####################################################################")
        print (" ")

    # Create a NEW server socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Server bind to IP and port no.
    srv.bind(server_address)
    # Listen for incoming connections
    srv.listen(1)

    # TCP server loop
    while True:
        # Write to logger
        if backLogger == True:
            logger.info("DEBUG_THD_TCP_SERVER: Radar track TCP Server waiting for client connection...")
            logger.info("####################################################################")
            logger.info(" ")
        # Print statement
        else:
            print ("DEBUG_THD_TCP_SERVER: Radar track TCP Server waiting for client connection...")
            print ("####################################################################")
            print (" ")

        # Accept the client connection    
        connection, client_address = srv.accept()
        addrClient = str(client_address)

        # After received client connection, start TCP Server loop
        try:
            # Write to logger
            if backLogger == True:
                # log events, when daemon run in background
                logger.info("DEBUG_THD_TCP_SERVER: Radar track TCP Server get client connection from %s " % (addrClient))
                logger.info("####################################################################")
                logger.info(" ")
            # Print statement
            else:
                print ("DEBUG_THD_TCP_SERVER: Radar track TCP Server get client connection from %s" % (addrClient))
                print ("####################################################################")
                print (" ")

            # Connection with client established
            clientStatus = True

            # Check received text message data from Node Red client
            while clientStatus:
                # Listen for text message data from Node Red client
                rxTextMsgData = connection.recv(4096)
                rxTextMsgData = rxTextMsgData.decode()
                
                # Received text message data from Node Red client
                if rxTextMsgData != '':
                    # Write to logger
                    if backLogger == True:
                        # log events, when daemon run in background
                        logger.info("DEBUG_THD_TCP_SERVER: Received text message data: [%s]" % (rxTextMsgData))
                        logger.info("####################################################################")
                        logger.info(" ")
                    # Print statement
                    else:
                        print ("DEBUG_THD_TCP_SERVER: Received text message data: [%s]" % (rxTextMsgData))
                        print ("####################################################################")
                        print (" ")

                    # Broadcast text message to the mesh nodes
                    meshSerInterface.sendText(rxTextMsgData)
                    rxTextMsgData = ''
                    
                # Connection with server closed
                if rxTextMsgData == b'':
                    # Write to logger
                    if backLogger == True:
                        logger.info("DEBUG_THD_TCP_SERVER: Client disconnected!")
                        logger.info("####################################################################")
                        logger.info(" ")
                    # Print statement
                    else:
                        print ("DEBUG_THD_TCP_SERVER: Client disconnected!")
                        print ("####################################################################")
                        print (" ")

                    rxTextMsgData = ''
                    
                    # Reset the connection
                    # Clean up the connection
                    connection.close()
                    clientStatus = False

        # TCP server error      
        except:
            # Write to logger
            if backLogger == True:
                logger.info("DEBUG_THD_TCP_SERVER: Client loop ERROR!")
                logger.info("####################################################################")
                logger.info(" ")
            else:
                print ("DEBUG_THD_TCP_SERVER: Client loop ERROR!")
                print ("####################################################################")
                print (" ")
                
            # Reset the connection
            # Clean up the connection
            connection.close()
            clientStatus = False 

# Thread to send a mesh related nodes data
def thread_tcpClient_NodeRed (name, sleepLoop):
    global sendTcpDataType
    global meshNodeListData
    global tcpIpAddr 
    global tcpPortNoMesh

    nodeUserData = ''
    
    # Forever loop                                   
    while True:
        time.sleep(sleepLoop)

        # Send available mesh nodes data to the node red TCP server
        if sendTcpDataType == 1:
            # Try to send the data
            try:
                # Socket initialization                
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((tcpIpAddr, tcpPortNoMesh))

                # Get the total record in dictionary
                totRecord = len(meshNodeListData)
                
                # Loop through mesh nodes data dictionary
                for a in range(0, (totRecord)):
                    tempData = '!' + meshNodeListData[a]['No'] + '!' + meshNodeListData[a]['User'] + '!' + meshNodeListData[a]['AKA'] + \
                               '!' + meshNodeListData[a]['ID'] + '!' + meshNodeListData[a]['Latitude'] + '!' + meshNodeListData[a]['Longitude'] + \
                               '!' + meshNodeListData[a]['Altitude'] + '!' + meshNodeListData[a]['Battery'] + '!' + meshNodeListData[a]['SNR'] + \
                               '!' + meshNodeListData[a]['LastHeard'] + '!' + meshNodeListData[a]['Since']

                    # Append the data
                    nodeUserData += tempData
                    tempData = ''
                
                # Write to logger - For debugging
                if backLogger == True:
                    logger.info("DEBUG_THD_TCP_CLIENT: Sending mesh nodes data: [%s}" % (nodeUserData))
                    logger.info(" ")
                    logger.info("####################################################################")
                    logger.info(" ")
                # Print statement
                else:
                    print ("DEBUG_THD_TCP_CLIENT: Sending mesh nodes data: [%s]" % (nodeUserData))
                    print (" ")
                    print ("####################################################################")
                    print (" ")

                # Send the mesh nodes data to node red TCP server
                sock.send(bytearray(nodeUserData.encode()))
                nodeUserData = ''
                # Close socket
                sock.close()
                sendTcpDataType = 0
                
            # Error during sending the data
            except:
                # Write to logger - For debugging
                if backLogger == True:
                    logger.info("DEBUG_THD_TCP_CLIENT: Error during sending the data!")
                    logger.info(" ")
                    logger.info("####################################################################")
                    logger.info(" ")
                # Print statement
                else:
                    print ("DEBUG_THD_TCP_CLIENT: Error during sending the data!")
                    print (" ")
                    print ("####################################################################")
                    print (" ")

                # Closed the socket
                sock.close()
    
# Thread for meshtastic serial interface, process mesh nodes data
def thread_serial_mesh(name, sleepLoop):
    global meshSerInterface 
    global backLogger
    global pollNodesCnt
    global meshNodeListData
    global firstNodesData
    global sendTcpDataType
    global ttyMeshNAvail
    global connSerialMeshCnt

    # Nodes data process variable
    nodesData = ''
    nodesColData = ''
    pdNodesData = ''
    oneChar = ''
    forCastOneChar = ''
    nodesIndx = 0
    lengthColData = 0
    rowCnt = 3
    
    # Nodes data for each devices
    nodeNumber = ''
    nodeUser = ''
    nodeAka = ''
    nodeId = ''
    nodeLat = ''
    nodeLon = ''
    nodeAlt = ''
    nodeBatt = ''
    nodeSnr = ''
    nodeLastHeard = ''
    nodeSince = ''
    
    unicodeChar = False
    unicodeCharFnd = False
    unicodeBuff = ''
    
    nodeNumbFnd = False
    usrNameFnd = False
    akaFnd = False
    nodeIdFnd = False
    nodeLatFnd = False
    nodeLonFnd = False
    nodeAltFnd = False
    nodeBattFnd = False
    nodeSnrFnd = False
    nodeLastHeardFnd = False
    startExtract = False
    endOfLatLon = False
    endOfAlt = False
    endOfBatt = False
    endOfSnr = False
    updateRecord = False
    
    # Forever loop
    while True:
        # Loop every 1s
        time.sleep(sleepLoop)

        # Previously MESH TTY already been created
        if ttyMeshNAvail == True:
            pollNodesCnt += 1
            # Reach 1 minutes, start poll the mesh nodes
            if pollNodesCnt == 5 or firstNodesData == False:
                # Redirect the results to stdout
                # No need to display tabular data
                f = StringIO()
                with redirect_stdout(f):
                    # Get the current mesh nodes data
                    nodesData = meshSerInterface.showNodes()

                # Start gets the nodes record count
                # First, get the total mesh nodes raw data 
                # Second, get the record count summary
                # Lastly, get the total mesh nodes actual records
                pdNodesData = pd.read_table(StringIO(nodesData), header=0)
                nodesIndx = pdNodesData.index
                nodesIndx = len(nodesIndx)

                # Go through each index
                for a in range(0, (nodesIndx)):
                    # Read mesh nodes data according to the current row index
                    if a == rowCnt:
                        # Read the row nodes data table using pandas 
                        pdNodesData = pd.read_table(StringIO(nodesData), header=a)
                        # Get the selected columns data
                        nodesColData = pdNodesData.columns 
                                    
                        # Write to logger - For debugging
                        if backLogger == True:
                            logger.info("DEBUG_THD_SERIAL_MESH: Unprocess ROW mesh node data: %s" % (nodesColData))
                            print (" ")
                            logger.info("####################################################################")
                            logger.info(" ")
                        # Print statement
                        else:
                            print ("DEBUG_THD_SERIAL_MESH: Unprocess ROW mesh node data: %s" % (nodesColData))
                            print (" ")
                            print ("####################################################################")
                            print (" ")

                        # Convert pandas object to string
                        nodesColData = str(nodesColData)
                        # Convert string to ascii
                        nodesColData = ascii(nodesColData)
                        # Get the column data length
                        lengthColData = len(nodesColData)

                        # Write to logger - For debugging
                        if backLogger == True:
                            logger.info("DEBUG_THD_SERIAL_MESH: ASCII ROW mesh node data: %s" % (nodesColData))
                            logger.info(" ")
                            logger.info("####################################################################")
                            logger.info(" ")
                        # Print statement
                        else:
                            print ("DEBUG_THD_SERIAL_MESH: ASCII ROW mesh node data: %s" % (nodesColData))
                            print (" ")
                            print ("####################################################################")
                            print (" ")
                            
                        # Go through the data
                        for b in range(0, (lengthColData + 1)):
                            oneChar = mid(nodesColData, b, 1)
                            # Check for unicode data separator
                            if unicodeCharFnd == False:
                                # Start checking for unicode char - '\' character
                                if oneChar == chr(92) and unicodeChar == False:
                                    unicodeChar = True
                                # Found '\' character
                                elif unicodeChar == True:
                                    # Check for digit
                                    if oneChar.isdigit(): 
                                        unicodeBuff += oneChar
                                        # Complete unicode data
                                        if unicodeBuff == '2502':
                                            unicodeCharFnd = True

                            # Check for mesh nodes data
                            else:
                                # Forecast next character to find '\' character
                                forCastOneChar = mid(nodesColData, b + 1, 1)
                                # Search for node number
                                if nodeNumbFnd == False:
                                    # Confirm next character are '\'
                                    if forCastOneChar == chr(92):
                                        nodeNumbFnd = True
                                        
                                        # Reset necessary variable
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''
                                    
                                    # Start get the record number index
                                    else:
                                        # Check for digit
                                        if oneChar.isdigit():
                                            nodeNumber += oneChar
                                            
                                # Search for node user name
                                elif usrNameFnd == False:
                                    # Confirm next character are '\'
                                    if forCastOneChar == chr(92):
                                        usrNameFnd = True
                                        
                                        # Reset necessary variable
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''
                                    
                                    # Start get the node user name
                                    else:
                                        # Check for first character
                                        if startExtract == False:
                                            # Check for the first character either alphabet or number
                                            if oneChar.isalpha() or oneChar.isdigit():
                                                nodeUser += oneChar
                                                startExtract = True

                                        # Start append data
                                        elif startExtract == True:
                                            nodeUser += oneChar

                                # Search for node a.k.a
                                elif akaFnd == False:
                                    # Confirm next character are '\'
                                    if forCastOneChar == chr(92):
                                        akaFnd = True
                                        
                                        # Reset necessary variable
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''
                                    
                                    # Start get the node a.k.a
                                    else:
                                        # Check for first character
                                        if startExtract == False:
                                            # Check for the first character either alphabet or number
                                            if oneChar.isalpha() or oneChar.isdigit():
                                                nodeAka += oneChar
                                                startExtract = True

                                        # Start append data
                                        elif startExtract == True:
                                            if oneChar != ' ':
                                                nodeAka += oneChar
                                            
                                # Search for node ID
                                elif nodeIdFnd == False:
                                    # Confirm next character are '\'
                                    if forCastOneChar == chr(92):
                                        nodeIdFnd = True
                                        
                                        # Reset necessary variable
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''
                                    
                                    # Start get the node ID
                                    else:
                                        # Check for first character
                                        if startExtract == False:
                                            # Check for the first character either alphabet or number
                                            if oneChar.isalpha() or oneChar.isdigit():
                                                nodeId += oneChar
                                                startExtract = True

                                        # Start append data
                                        elif startExtract == True:
                                            if oneChar != ' ':
                                                nodeId += oneChar
                                                
                                # Search for node latitude
                                elif nodeLatFnd == False:
                                    # Confirm next character are '0'
                                    if oneChar == '0' and endOfLatLon == True:
                                        nodeLat += " [degree]"
                                                
                                        nodeLatFnd = True
                                        
                                        # Reset necessary variable
                                        endOfLatLon = False
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''

                                    # Previously there is NO latitude data
                                    elif nodeLat == 'N/A' and endOfLatLon == True:
                                        nodeLatFnd = True
                                        
                                        # Reset necessary variable
                                        endOfLatLon = False
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''
                                    
                                    # Start get the node latitude
                                    else:
                                        # Check for first character
                                        if startExtract == False:
                                            # Check for the first character number
                                            if oneChar.isdigit():
                                                nodeLat += oneChar
                                                startExtract = True

                                            # Latitude data are not available
                                            elif oneChar == 'N' or oneChar == 'A' or oneChar == '/':
                                                nodeLat += oneChar

                                                if nodeLat == 'N/A':
                                                    endOfLatLon = True
                                                
                                        # Start append data
                                        elif startExtract == True:
                                            # Get the latitude data as long as not found unicode '\' character
                                            if endOfLatLon == False:
                                                if oneChar.isdigit() or oneChar == '.':
                                                    nodeLat += oneChar

                                                # End of latitude data - Found unicode '\' character
                                                elif oneChar == chr(92):
                                                    endOfLatLon = True

                                # Search for node longitude
                                elif nodeLonFnd == False:
                                    # Confirm next character are '0'
                                    if oneChar == '0' and endOfLatLon == True:
                                        nodeLon += " [degree]"

                                        nodeLonFnd = True
                                        
                                        # Reset necessary variable
                                        endOfLatLon = False
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''

                                    # Previously there is NO longitude data
                                    elif nodeLon == 'N/A' and endOfLatLon == True:
                                        nodeLonFnd = True
                                        
                                        # Reset necessary variable
                                        endOfLatLon = False
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = '' 
                                    
                                    # Start get the node longitude
                                    else:
                                        # Check for first character
                                        if startExtract == False:
                                            # Check for the first character number
                                            if oneChar.isdigit():
                                                nodeLon += oneChar
                                                startExtract = True

                                            # Longitude data are not available
                                            elif oneChar == 'N' or oneChar == 'A' or oneChar == '/':
                                                nodeLon += oneChar

                                                if nodeLon == 'N/A':
                                                    endOfLatLon = True

                                        # Start append data
                                        elif startExtract == True:
                                            # Get the longitude data as long as not found unicode '\' character
                                            if endOfLatLon == False:
                                                if oneChar.isdigit() or oneChar == '.':
                                                    nodeLon += oneChar

                                                # End of latitude data - Found unicode '\' character
                                                elif oneChar == chr(92):
                                                    endOfLatLon = True

                                # Search for node altitude
                                elif nodeAltFnd == False:
                                    # Confirm altitude data was completed
                                    if endOfAlt == True:
                                        nodeAltFnd = True
                                        
                                        # Reset necessary variable
                                        endOfAlt = False
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''
                                    
                                    # Start get the node altitude
                                    else:
                                        # Check for first character
                                        if startExtract == False:
                                            # Check for the first character number
                                            if oneChar.isdigit():
                                                nodeAlt += oneChar
                                                startExtract = True

                                            # Altitude data are not available
                                            elif oneChar == 'N' or oneChar == 'A' or oneChar == '/':
                                                nodeAlt += oneChar

                                                if nodeAlt == 'N/A':
                                                    endOfAlt = True

                                        # Start append data
                                        elif startExtract == True:
                                            if oneChar.isdigit() or oneChar == '.' or oneChar == 'm':
                                                nodeAlt += oneChar
                                                # End of altitude data
                                                if 'm' in nodeAlt:
                                                    endOfAlt = True

                                # Search for node battery status
                                elif nodeBattFnd == False:
                                    # Confirm next character are '\'
                                    if forCastOneChar == chr(92):
                                        nodeBatt += "[VDC]"
                                            
                                        nodeBattFnd = True
                                        
                                        # Reset necessary variable
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''

                                    # Previously there is no battery voltage data
                                    elif endOfBatt == True:
                                        nodeBattFnd = True
                                        
                                        # Reset necessary variable
                                        endOfBatt = False
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''
                                    
                                    # Start get the node battery status
                                    else:
                                        # Check for first character
                                        if startExtract == False:
                                            # Check for the first character number
                                            if oneChar.isdigit():
                                                nodeBatt += oneChar
                                                startExtract = True

                                            # Battery data are not available
                                            elif oneChar == 'N' or oneChar == 'A' or oneChar == '/':
                                                nodeBatt += oneChar

                                                if nodeBatt == 'N/A':
                                                    endOfBatt = True

                                        # Start append data
                                        elif startExtract == True:
                                            if oneChar.isdigit() or oneChar == '.' or oneChar == 'N' or oneChar == 'A' or oneChar == '/':
                                                nodeBatt += oneChar
                                        
                                # Search for node SNR status
                                elif nodeSnrFnd == False:
                                    # Confirm SNR data was completed
                                    if endOfSnr == True:
                                        nodeSnrFnd = True
                                        
                                        # Reset necessary variable
                                        endOfSnr = False
                                        startExtract = False
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''
                                    
                                    # Start get the node SNR status
                                    else:
                                        # Check for first character
                                        if startExtract == False:
                                            # Check for the first character number
                                            if oneChar.isdigit():
                                                nodeSnr += oneChar
                                                startExtract = True

                                            # SNR data are not available
                                            elif oneChar == 'N' or oneChar == 'A' or oneChar == '/':
                                                nodeSnr += oneChar

                                                if nodeSnr == 'N/A':
                                                    endOfSnr = True

                                        # Start append data
                                        elif startExtract == True:
                                            if oneChar.isdigit() or oneChar == '.' or oneChar == 'd' or oneChar == 'B':
                                                nodeSnr += oneChar
                                                # End of SNR data
                                                if 'B' in nodeSnr:
                                                    endOfSnr = True
                                
                                # Search for node last heard
                                elif nodeLastHeardFnd == False:
                                    # Confirm next character are '\'
                                    if forCastOneChar == chr(92):
                                        nodeLastHeardFnd = True
                                        
                                        # Reset necessary variable
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''
                                    
                                    # Start get the node last heard
                                    else:
                                        nodeLastHeard += oneChar

                                # Search for node since - End of data for selected row
                                elif nodeNumbFnd == True and usrNameFnd == True and akaFnd == True and nodeIdFnd == True and nodeLatFnd == True and nodeLonFnd == True \
                                     and nodeAltFnd == True and nodeBattFnd == True and nodeSnrFnd == True and nodeLastHeardFnd == True:
                                    # Confirm next character are '\'
                                    if forCastOneChar == chr(92):
                                        # Write to logger - For debugging
                                        if backLogger == True:
                                            logger.info("DEBUG_THD_SERIAL_MESH: No: [%s], User Name: [%s], A.K.A: [%s], Node ID: [%s], Lat: [%s], Lon: [%s], Alt: [%s], \
                                            Batt: [%s], SNR: [%s], LastHeard: [%s], Since: [%s]" % (nodeNumber, nodeUser, nodeAka, nodeId, nodeLat, nodeLon, nodeAlt, \
                                            nodeBatt, nodeSnr, nodeLastHeard, nodeSince))
                                            logger.info(" ")
                                            logger.info("####################################################################")
                                            logger.info(" ")

                                        else:
                                            print ("DEBUG_THD_SERIAL_MESH: No: [%s], User Name: [%s], A.K.A: [%s], Node ID: [%s], Lat: [%s], Lon: [%s], Alt: [%s], \
                                            Batt: [%s], SNR: [%s], LastHeard: [%s], Since: [%s]" % (nodeNumber, nodeUser, nodeAka, nodeId, nodeLat, nodeLon, nodeAlt, \
                                            nodeBatt, nodeSnr, nodeLastHeard, nodeSince))
                                            print (" ")
                                            print ("####################################################################")
                                            print (" ")

                                        # Update python data dictionary with retrieve nodes info
                                        try:
                                            # Check the nodes ID whether its already exist or not
                                            # Update the existing data, throw error if record is not exist, consider its a new data
                                            nodeDB = [ nodeDBB for nodeDBB in meshNodeListData if (nodeDBB['ID'] == nodeId) ]

                                            # There is a changes on the mesh data that need to be send to the node red TCP server
                                            if nodeLat != nodeDB[0]['Latitude'] or nodeLon != nodeDB[0]['Longitude']:
                                                # Set the updating flag
                                                updateRecord = True
                                                
                                            nodeDB[0]['No'] = nodeNumber
                                            nodeDB[0]['User'] = nodeUser
                                            nodeDB[0]['AKA'] = nodeAka
                                            nodeDB[0]['ID'] = nodeId
                                            nodeDB[0]['Latitude'] = nodeLat
                                            nodeDB[0]['Longitude'] = nodeLon
                                            nodeDB[0]['Altitude'] = nodeAlt
                                            nodeDB[0]['Battery'] = nodeBatt
                                            nodeDB[0]['SNR'] = nodeSnr
                                            nodeDB[0]['LastHeard'] = nodeLastHeard
                                            nodeDB[0]['Since'] = nodeSince

                                                                                       
                                        # New mesh nodes data
                                        except:
                                            # Construct the new data
                                            newData = {
                                                        'No' : nodeNumber,
                                                        'User' : nodeUser,
                                                        'AKA' : nodeAka,
                                                        'ID' : nodeId,
                                                        'Latitude' : nodeLat,
                                                        'Longitude' : nodeLon,
                                                        'Altitude' : nodeAlt,
                                                        'Battery' : nodeBatt,
                                                        'SNR' : nodeSnr,
                                                        'LastHeard' : nodeLastHeard,
                                                        'Since' : nodeSince,
                                                        }
                                            # Append a NEW extension to the existing record
                                            meshNodeListData.append(newData)
                                            # NEW mesh data, need to send to node red TCP server, set the flag
                                            updateRecord = True
                                        
                                        # Reset necessary variable
                                        nodeNumbFnd = False
                                        usrNameFnd = False
                                        akaFnd = False
                                        nodeIdFnd = False
                                        nodeLatFnd = False
                                        nodeLonFnd = False
                                        nodeAltFnd = False
                                        nodeBattFnd = False
                                        nodeSnrFnd = False
                                        nodeLastHeardFnd = False
                                                   
                                        nodeNumber = ''
                                        nodeUser = ''
                                        nodeAka = ''
                                        nodeId = ''
                                        nodeLat = ''
                                        nodeLon = ''
                                        nodeAlt = ''
                                        nodeBatt = ''
                                        nodeSnr = ''
                                        nodeLastHeard = ''
                                        nodeSince = ''
                                                   
                                        unicodeCharFnd = False
                                        unicodeChar = False
                                        unicodeBuff = ''
                                        
                                        # Initialize back row counter by factor of 2
                                        rowCnt += 2
                                                   
                                        # Exit current loop
                                        break

                                # Start get the node since
                                    else:
                                        nodeSince += oneChar
                                
                pollNodesCnt = 0
                rowCnt = 3

                # First poll initialization, at least mesh gateway node
                if firstNodesData == False:
                    firstNodesData = True
                    # Send the first mesh data to the node red TCP server
                    sendTcpDataType = 1

                # There is need to update a mesh nodes data to node red TCP server
                elif updateRecord == True:
                    # Send the first mesh data to the node red TCP server
                    sendTcpDataType = 1
                    updateRecord = False
                    
                # Write to logger - For debugging
                if backLogger == True:
                    logger.info("DEBUG_THD_SERIAL_MESH: Device Nodes JSON Data:")
                    logger.info(meshNodeListData)
                    logger.info(" ")
                    logger.info("####################################################################")
                    logger.info(" ")
                # Print statement
                else:
                    print ("DEBUG_THD_SERIAL_MESH: Device Nodes JSON Data:")
                    print (meshNodeListData)
                    print (" ")
                    print ("####################################################################")
                    print (" ")

        # Previously TTY MESH are not created, retry to connect
        else:
            # MESH API initialization attempt
            try:
                # Increment initialization counter
                connSerialMeshCnt += 1
                # Every 10s try to initialize the MESH serial API
                if connSerialMeshCnt == 10:
                    meshSerInterface = meshtastic.SerialInterface()

                    connSerialMeshCnt = 0
                    ttyMeshNAvail = True

                    # Write to logger - For debugging
                    if backLogger == True:
                        logger.info("DEBUG_THD_SERIAL_MESH: MESH API initialization SUCCESSFUL")
                        logger.info(" ")
                        logger.info("####################################################################")
                        logger.info(" ")
                    # Print statement
                    else:
                        print ("DEBUG_THD_SERIAL_MESH: MESH API initialization SUCCESSFUL")
                        print (" ")
                        print ("####################################################################")
                        print (" ")

            # Error during MESH API initialization
            except:
                # Write to logger - For debugging
                if backLogger == True:
                    logger.info("DEBUG_THD_SERIAL_MESH: MESH API initialization FAILED!, retry attempt in 10s")
                    logger.info(" ")
                    logger.info("####################################################################")
                    logger.info(" ")
                # Print statement
                else:
                    print ("DEBUG_THD_SERIAL_MESH: MESH API initialization FAILED!, retry attempt in 10s")
                    print (" ")
                    print ("####################################################################")
                    print (" ")

                connSerialMeshCnt = 0
                ttyMeshNAvail = False
            
# Main daemon entry point    
def main():
    global secureInSecure
    global meshSerInterface
    global ttyMeshNAvail

    pub.subscribe(onReceive, "meshtastic.receive")
    pub.subscribe(onConnection, "meshtastic.connection.established")

    # Initialize device API - Try first
    try:
        meshSerInterface = meshtastic.SerialInterface()
        ttyMeshNAvail = True

        # Write to logger - For debugging
        if backLogger == True:
            logger.info("DEBUG_MAIN: MESH API initialization SUCCESSFUL")
            logger.info(" ")
            logger.info("####################################################################")
            logger.info(" ")
        # Print statement
        else:
            print ("DEBUG_MAIN: MESH API initialization SUCCESSFUL")
            print (" ")
            print ("####################################################################")
            print (" ")
                        
    # Error, TTY not created yet 
    except:
        ttyMeshNAvail = False
        # Write to logger - For debugging
        if backLogger == True:
            logger.info("DEBUG_MAIN: MESH serial interface NOT ready!")
            logger.info(" ")
            logger.info("####################################################################")
            logger.info(" ")
        # Print statement
        else:
            print ("DEBUG_MAIN: MESH serial interface NOT ready!")
            print (meshNodeListData)
            print (" ")
            print ("####################################################################")
            print (" ")
        
    
    # Initialize thread for meshtastic serial interface
    threadSerMesh = threading.Thread(target=thread_serial_mesh, args=(1,1), daemon=True)
    # Start the thread
    threadSerMesh.start()

    # Initialize thread for node red tcp client 
    threadNodeRedTcp = threading.Thread(target=thread_tcpClient_NodeRed, args=(1,1), daemon=True)
    # Start the thread
    threadNodeRedTcp.start()

    # Initialize thread for TCP server
    threadTcpServer = threading.Thread(target=thread_tcpServer_NodeRed, args=(1, ), daemon=True)
    # Start the thread
    threadTcpServer.start()
    
    if secureInSecure == True:
        # RUN RestFul API web server
        # Add a certificate to make sure REST web API can support HTTPS request
        # Generate first cert.pem (new certificate) and key.pem (new key) by initiate below command:
        # openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365
        #app.run(host='0.0.0.0', port=8000, ssl_context=('cert.pem', 'key.pem'))
        #app.run(host='0.0.0.0', port=8000, ssl_context=('asterisk.pem', 'ca.key'))
        app.run(host='0.0.0.0', port=9000, ssl_context=('asterisk.pem', 'ca.key'))
    # Insecure web server (HTTP) - Default port 5000
    else:
        app.run(host='0.0.0.0', port=9000)        
if __name__ == "__main__":
    main()
