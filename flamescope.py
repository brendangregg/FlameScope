#!/usr/bin/python
#
# flamescope	Explore trace files with heat maps and flame graphs.
#               Processes Linux "perf script" ouptut.
#
# Copyright 2018 Netflix, Inc.
# Licensed under the Apache License, Version 2.0 (the "License")
#
# 23-Feb-2017	Brendan Gregg	Created this.
# 19-Feb-2018	   "      "     Rewrite Perl -> Python.

import sys
import time
import BaseHTTPServer
import re
import argparse
import urlparse
import json
from math import ceil, floor

# arguments
parser = argparse.ArgumentParser(description="Flame Scope")
# input must be a file and not stdin, as it may be large (> 1 Gbyte) so we
# don't want to cache it in memory, and, we can't seek backwards in stdin.
parser.add_argument("infile", nargs=1,
    help="input filename (output of perf script)")
parser.add_argument("-p", "--port", default=8008)
args = parser.parse_args()

# global defaults
debug = 0       # debug messages
rows = 50       # heatmap rows
yratio = 1000   # milliseconds

# global vars
offsets = []
start = -1      # starting timestamp
end = -1        # ending timestamp
root = {}

#
# Parsing
#
# perf script output examples:
#
# Stack examples (-g):
#
# swapper     0 [021] 28648.467059: cpu-clock: 
#	ffffffff810013aa xen_hypercall_sched_op ([kernel.kallsyms])
#	ffffffff8101cb2f default_idle ([kernel.kallsyms])
#	ffffffff8101d406 arch_cpu_idle ([kernel.kallsyms])
#	ffffffff810bf475 cpu_startup_entry ([kernel.kallsyms])
#	ffffffff81010228 cpu_bringup_and_idle ([kernel.kallsyms])
#
# java 14375 [022] 28648.467079: cpu-clock: 
#	    7f92bdd98965 Ljava/io/OutputStream;::write (/tmp/perf-11936.map)
#	    7f8808cae7a8 [unknown] ([unknown])
#
# swapper     0 [005]  5076.836336: cpu-clock: 
#	ffffffff81051586 native_safe_halt ([kernel.kallsyms])
#	ffffffff8101db4f default_idle ([kernel.kallsyms])
#	ffffffff8101e466 arch_cpu_idle ([kernel.kallsyms])
#	ffffffff810c2b31 cpu_startup_entry ([kernel.kallsyms])
#	ffffffff810427cd start_secondary ([kernel.kallsyms])
#
# swapper     0 [002] 6034779.719110:   10101010 cpu-clock: 
#       2013aa xen_hypercall_sched_op+0xfe20000a (/lib/modules/4.9-virtual/build/vmlinux)
#       a72f0e default_idle+0xfe20001e (/lib/modules/4.9-virtual/build/vmlinux)
#       2392bf arch_cpu_idle+0xfe20000f (/lib/modules/4.9-virtual/build/vmlinux)
#       a73333 default_idle_call+0xfe200023 (/lib/modules/4.9-virtual/build/vmlinux)
#       2c91a4 cpu_startup_entry+0xfe2001c4 (/lib/modules/4.9-virtual/build/vmlinux)
#       22b64a cpu_bringup_and_idle+0xfe20002a (/lib/modules/4.9-virtual/build/vmlinux)
#
# bash 25370/25370 6035935.188539: cpu-clock: 
#                   b9218 [unknown] (/bin/bash)
#                 2037fe8 [unknown] ([unknown])
# other combinations are possible.
#
# Some extra examples (excluding stacks):
#
# java 52025 [026] 99161.926202: cycles: 
# java 14341 [016] 252732.474759: cycles:      7f36571947c0 nmethod::is_nmethod() const (/...
# java 14514 [022] 28191.353083: cpu-clock:      7f92b4fdb7d4 Ljava_util_List$size$0;::call (/tmp/perf-11936.map)
#      swapper     0 [002] 6035557.056977:   10101010 cpu-clock:  ffffffff810013aa xen_hypercall_sched_op+0xa (/lib/modules/4.9-virtual/build/vmlinux)
#         bash 25370 6036.991603:   10101010 cpu-clock:            4b931e [unknown] (/bin/bash)
#         bash 25370/25370 6036036.799684: cpu-clock:            4b913b [unknown] (/bin/bash)
# other combinations are possible.
#
# This event_regexp matches the event line, and puts time in the first group:
#
event_regexp = " +([0-9.]+): .+?:"
comm_regexp = "^ *([^0-9]+)"
# idle stack identification. just a regexp for now:
idle_process = "swapper";
idle_stack = "(cpuidle|cpu_idle|cpu_bringup_and_idle|native_safe_halt|xen_hypercall_sched_op|xen_hypercall_vcpu_op)";
idle_regexp = "%s.*%s" % (idle_process, idle_stack)
frame_regexp = "^[\t ]*[0-9a-fA-F]+ ([^ +]+)"

# debug print
def dprint(string):
    global debug
    if (debug):
        print("%s %s" % (time.asctime(), string))

# read and cache offsets
def readoffsets(infilename):
    global start
    global end
    global offsets

    try:
        infile = open(infilename, 'r')
    except:
        print("ERROR: Can't read infile %s. Exiting" % infilename)
        exit()

    stack = ""
    ts = -1
    # process perf script output and search for two things:
    # - event_regexp: to identify event timestamps
    # - idle_regexp: for filtering idle stacks
    # this populates start, end, and offsets
    for line in infile.readlines():
        if (re.search(r"^#", line)):
            continue
        r = re.search(event_regexp, line)
        if (r):
            if (stack != ""):
                # process prior stack
                if (not re.search(idle_regexp, stack)):
                    offsets.append(ts)
                # don't try to cache stacks (could be many Gbytes):
                stack = ""
            ts = float(r.group(1))
            if (start == -1):
                start = ts
            stack = line.rstrip()
        else:
            stack += line.rstrip()
    # last stack
    if (not re.search(idle_regexp, stack)):
        offsets.append(ts)
    if (ts > end):
        end = ts

    infile.close

def sendheaders(self, status):
    self.send_response(status)
    self.send_header("Content-type", "text/html")
    self.send_header("Access-Control-Allow-Origin", "*")
    self.end_headers()

# return a heatmap json from the cached offsets
def heatmap(handler, query):
    global start
    global end
    global rows
    global offsets
    maxvalue = 0

    sendheaders(handler, 200)

    q = urlparse.parse_qs(query)
    if (q and q['rows']):
        rows = int(q['rows'][0])

    rowoffsets = []
    for i in range(0, rows):
        rowoffsets.append(yratio * (float(i) / rows))
    cols = int(ceil(end) - floor(start))
    timeoffsets = range(0, cols)
    # init cells (values) to zero
    values = []
    for i in range(0, cols):
        emptycol = []
        for i in range(0, rows):
            emptycol.append(0)
        values.append(emptycol)
    # increment heatmap cells
    for ts in offsets:
        col = int(floor(ts - floor(start)))
        row = int(floor(rows * (ts % 1)))
        values[col][row] += 1
        if (values[col][row] > maxvalue):
            maxvalue = values[col][row]

    # emit json
    heatmap = {}
    heatmap['rows'] = rowoffsets
    heatmap['columns'] = timeoffsets
    heatmap['values'] = values
    heatmap['maxvalue'] = maxvalue
    handler.wfile.write(json.dumps(heatmap))

# add a stack to the root tree
def addstack(root, stack):
    root['value'] += 1
    last = root
    for name in stack:
        found = 0
        for child in last['children']:
            if child['name'] == name:
                last = child
                found = 1
                break
        if (found):
            last['value'] += 1
        else:
            newframe = {}
            newframe['children'] = []
            newframe['name'] = name
            newframe['value'] = 1
            last['children'].append(newframe)
            last = newframe

# return stack samples for a given range
def samplerange(handler, infilename, query):
    global start
    global end
    global root

    # fetch and check range. default to full range if not specified.
    rangestart = start 
    rangeend = end
    q = urlparse.parse_qs(query)
    if (q and q['start']):
        rangestart = float(q['start'][0]) + start
    if (q and q['end']):
        rangeend = float(q['end'][0]) + start
    if ((rangestart < start or rangeend > end) or (rangestart > rangeend)):
        # bad range
        dprint("bad range: %s - %s" % (q['start'][0], q['end'][0]))
        sendheaders(handler, 416)
        return
    sendheaders(handler, 200)

    try:
        infile = open(infilename, 'r')
    except:
        print("ERROR: Can't read infile %s. Exiting" % infilename)
        exit()

    root = {}
    root['children'] = []
    root['name'] = "root"
    root['value'] = 0

    stack = []
    ts = -1
    # process perf script output and search for two things:
    # - event_regexp: to identify event timestamps
    # - idle_regexp: for filtering idle stacks
    # this populates start, end, and offsets
    for line in infile.readlines():
        if (re.search(r"^#", line)):
            continue
        r = re.search(event_regexp, line)
        if (r):
            if (stack):
                # process prior stack
                if (re.search(idle_regexp, ";".join(stack))):
                    # skip idle
                    stack = []
                elif (ts >= rangestart and ts <= rangeend):
                    addstack(root, stack)
                stack = []
            ts = float(r.group(1))
            r = re.search(comm_regexp, line)
            if (r):
                stack.append(r.group(1).rstrip())
            else:
                stack.append("<unknown>")
        else:
            r = re.search(frame_regexp, line)
            if (r):
                stack.insert(1, r.group(1))
    # last stack
    if (ts >= rangestart and ts <= rangeend):
        addstack(root, stack)

    # emit json
    handler.wfile.write(json.dumps(root))

    infile.close

# parse requests
class WebHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    def do_HEAD(self):
        sendheaders(self, 200)
    def do_GET(self):
        url = urlparse.urlparse(self.path)
        # /range
        if (url.path == '/range'):
            dprint("Range request: %s" % self.path)
            samplerange(self, args.infile[0], url.query)
        # /heatmap
        elif (url.path == '/heatmap'):
            dprint("Heatmap request: %s" % self.path)
            heatmap(self, url.query)
        else:
            dprint("Invalid request: %s" % self.path)
            sendheaders(self, 404)

# main
if __name__ == '__main__':
    print("%s Loading infile: %s" % (time.asctime(), args.infile[0]))
    readoffsets(args.infile[0])
    dprint("Loaded... Starting Server...")

    server_class = BaseHTTPServer.HTTPServer
    httpd = server_class(('127.0.0.1', int(args.port)), WebHandler)
    print("%s Server start: http://127.0.0.1:%s" % (time.asctime(), args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    dprint("Server stopped")
