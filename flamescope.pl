#!/usr/bin/perl -w
#
# flamescope	Explore trace files with heat maps and flame graphs.
#		Currently supports Linux "perf script" output.
#
# Created as an experimental prototype. This will be turned into a real tool,
# which should also be open sourced.
#
# Copyright 2017 Netflix, Inc.
# Licensed under the Apache License, Version 2.0 (the "License")
#
# 23-Feb-2017	Brendan Gregg	Created this.

use strict;
use IO::Socket;
use Net::hostent;
use Getopt::Long;
use File::Basename;

# dependent programs
my $lib = dirname(__FILE__) . "/lib";
my $hmprog = "$lib/trace2heatmap.pl";
my $scprog = "$lib/stackcollapse-perf.pl";
my $fgprog = "$lib/flamegraph.pl";
my $rpprog = "$lib/range-perf.pl";
my $opprog = "$lib/offsets-perf.pl";
my $hbprog = "$lib/heatmap-browse.js";

# usage and options
sub usage {
	die <<USAGE_END;
USAGE: $0 perf_script_output.txt[.gz]
	--verbose	# more messages while running
	--slow   	# slow drawing of visualizations
USAGE_END
}
my $verbose = 0;
my $slow = 0;
GetOptions(
	'verbose'	=> \$verbose,
	'slow'		=> \$slow,
) or usage();

# check input file exists
my $file = $ARGV[0];
usage() unless defined $file and -r $file;
my $cat = "cat";
$cat = "gunzip -c" if $file =~ /\.gz$/;

# check dependent programs
my $prog;
foreach $prog ($hmprog, $scprog, $fgprog, $rpprog, $opprog) {
	if (not -x $prog) {
		print "ERROR: Can't find/execute $prog, which is required. " .
		    "Check setup. Exiting.\n";
		exit;
	}
}
foreach $prog ($hbprog) {
	if (not -r $prog) {
		print "ERROR: Can't find/read $prog, which is required. " .
		    "Check setup. Exiting.\n";
		exit;
	}
}

# generate heat map
print "Generating heat map from $file... ";
my $title = $file;
$title =~ s:.*/::;
# XXX figure out sample rate and set --rows properly
my $cmd = "$cat $file | $opprog --ms | $hmprog --rows=49 --unitslabel=ms " .
    "--title=\"$title\"";
open(HM, "$cmd|") or die "Error generating heatmap ($cmd): $!. Exiting.\n";
my @heatmap = <HM>;
close(HM);
print "done\n";

# setup web server
my $PORT = 9000;
my $server = IO::Socket::INET->new(Proto     => "tcp",
				   LocalPort => $PORT,
				   Listen    => SOMAXCONN,
				   Reuse     => 1);
die "ERROR: Can't setup web server to listen on port $PORT" unless $server;
print "Server running on http://127.0.0.1:$PORT\n";

# declare HTML/JavaScript
my $html_heatmap_top = '
<html>
<head><title>perf sample Explorer</title>
<style type="text/css">
rectclass:hover
{
	colorHover = "#FFFFFF";
	backgroundColorHover = "#000000";
}
</style>
<script type="text/ecmascript">
' . `cat $hbprog` . '
</script>
</head>
<body onload="pageinit();" style="font-family: Avenir, Arial, Open Sans Light, sans-serif;">

<center>
<h2 style="margin-bottom:5px">FlameScope</h2>
<p style="margin-bottom:0px;margin-top:0px">
';

my $html_flamegraph_top = '
<html>
<head><title>flamegraph</title>
<script type="text/ecmascript">
function pageinit()
{
	var loading = document.getElementById("loading");
	loading.innerHTML = "";
}
</script>
</head>
<body onload="pageinit();" style="font-family: Avenir, Arial, Open Sans Light, sans-serif;">
<center>
<p><span id="loading">Flamegraph loading, please wait... </span><a href="http://127.0.0.1:9000/">Back to main page</a>, or use back arrow.</p>
';

my $html_heatmap_form = '
<p style="margin-bottom:5px;margin-top:1px">To generate a flamegraph, click a range above or fill out these fields below:</p>
<form name="rangeform" action="/doflamegraph" onreset="resetrange();">
&nbsp; Start time: <input type="text" name="start" id="input_start">
End time: <input type="text" name="end" id="input_end">
<input type="submit" value="Submit">
<input type="reset" value="Reset">
</form>
<br>
';

my $html_bottom = '
</center>
</body>
</html>
';

# web server loop
my $url;
while (my $client = $server->accept()) {
  $client->autoflush(1);
  my $hostinfo = gethostbyaddr($client->peeraddr);
  printf "Connection from %s\n", $hostinfo ? $hostinfo->name : $client->peerhost
      if $verbose;

  while (<$client>) {
    $url = $_ if $_ =~ /^GET /;
    if (/^\s*$/) {
	print "$url" if $verbose;

	# heatmap page
	if ($url =~ /^GET \/ /) {

	    print $client "HTTP/1.0 200 OK\n\n";
	    print $client $html_heatmap_top;
	    for my $l (@heatmap) {
		print $client $l;
		select(undef, undef, undef, 0.0001) if $slow;
	    }
	    print $client $html_heatmap_form;
	    print $client $html_bottom;

	}

	# flamegraph
	elsif ($url =~ /^GET \/doflamegraph\?start=([0-9\.]+)\&end=([0-9\.]+)/) {
	    # GET /doflamegraph?start=13.114&end=18.513 HTTP/1.1
	    my ($start, $end) = ($1, $2);
	    print "Generating flame graph from $file range $start-$end...";
	    $cmd = "$cat $file | $rpprog --timezerosec $start $end | " .
	        "$scprog | grep -v cpu_idle | " .
	        "$fgprog --hash --color=java " .
	        "--title=\"$file: time $start - $end\"";
	    open(FG, "$cmd|") or die "Error generating flamegraph ($cmd): $!\n";

	    print $client "HTTP/1.0 200 OK\n\n";
	    print $client $html_flamegraph_top;
	    while ($_ = <FG>) {
		print $client $_;
		select(undef, undef, undef, 0.0001) if $slow;
	    }
	    close(FG);
	    print "done\n";
	    print $client $html_bottom;

	}

        close $client;
	last;
    }
  }
}
