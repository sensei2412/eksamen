# DRTP File Transfer Application

This archive provides the source code and documentation for the **DATA2410 Reliable Transport Protocol (DRTP)** file-transfer application, implemented in Python.

## Prerequisites

* Python 3.x installed on your system.
* No external Python dependencies beyond the standard library.

* Start mininet with sudo python3 simple.topo.py

## Running the Application

1. Open xterm h2 for server
2. Open xterm h1 for client
   

### Server (Receiver) on h2

1. Start the server on: Python3 application.py -s -i 10.0.1.2 -p 8080

   python3 application.py -s -i <SERVER_IP> - `-p`: UDP port to listen on (default: `8080`))
   - `-d`: (optional) simulate a one-time drop of packet with sequence number `DISCARD_SEQ`
   ```
   

### Client (Sender) on h1

1. In a separate terminal, still in the directory, run: Python3 applicaion.py -c -f Photo.jpg -i 10.0.1.2 -p 8080 -w 15
    
   ```bash
   python3 application.py -c -f <FILE_TO_SEND> -i <SERVER_IP> -p <PORT> -w <WINDOW_SIZE>
   ```

   * `-c` / `--client`: launch in client mode
   * `-f`: path to the file you wish to send
   * `-i`: IP address of the server
   * `-p`: UDP port of the server
   * `-w`: sender window size (default: `3`)

## Example Workflow

1. **Start server** on port 8080 (bind to all interfaces or localhost):

   ```bash
   # bind to all interfaces (default)
   python3 application.py -s -p 8080
   # or bind explicitly to server's LAN IP
   python3 application.py -s -i 10.0.1.2 -p 8080
   ```
2. **Send file** `photo.jpg` with window size 5:

   ```bash
   python3 application.py -c -f photo.jpg -i 10.0.1.2 -p 8080 -w 5
   ```
3. Observe the console logs on both sides for handshake, data-transfer, and teardown phases.
