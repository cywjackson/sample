#!/usr/bin/env python
#
# Extract a partictual thread dump by nid from the jstack -l <pid> output. 
# Output will be written to a file called /logs/[nid]-[timestamp].threaddump where [nid] is
# is from the input variable 

import sys 
import os
import os.path
import re

def startOfTDLog(nid):
    return re.compile('^"(?!C[1-2] CompilerThread).*nid='+nid+'.*')

def findStartOfTDLog(cpu, nid, stream):
    line = stream.readline()
    while line:
        if startOfTDLog(nid).match(line):
            return cpu +"% -" + line
        line = stream.readline()
    return None

def printToEndOfTDLog(stream, output):
    line = stream.readline()
    while line:
        if endOfTDLog.match(line):
            return line
        output.write(line)
        line = stream.readline()

cpu_1=sys.argv[1]
lwpid_1=sys.argv[2]
cpu_2=sys.argv[3]
lwpid_2=sys.argv[4]
timestamp=sys.argv[5]

nid=hex(int(lwpid_1))
nid2=hex(int(lwpid_2))

endOfTDLog = re.compile('^".*')
blankLine = re.compile('^$')

logFilename = "/logs/"+nid+"-"+timestamp+".threaddump"
# write the 1st thread
line = findStartOfTDLog(cpu_1, nid, sys.stdin)
while line:
#    print "Writing to ", logFilename
    output = open(logFilename, "w")
    output.write(line)
    line = printToEndOfTDLog(sys.stdin, output)
    output.close()
    break
# append the 2nd thread
line =  findStartOfTDLog(cpu_2, nid2, sys.stdin)
while line:
#    print "Writing to ", logFilename
    output = open(logFilename, "a")
    output.write(line)
    line = printToEndOfTDLog(sys.stdin, output)
    output.close()
    break

#    if not ( line and startOfTDLog.match(line)):
#        line = findStartOfTDLog(sys.stdin)

# terrible hack to just print the filename as the output result so we can wrap it as an execution cmd and set the filename variable for the caller
if os.path.isfile(logFilename):
    print nid+"-"+timestamp+".threaddump"
