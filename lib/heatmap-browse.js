/*
 * heatmap-browse.js
 *
 * This takes the output of trace2heatma.pl and adds interactivity for
 * flamescope. It's a separate file to make editing and syntax highlighting
 * easier.
 *
 * Copyright 2017 Netflix, Inc.
 * Licensed under the Apache License, Version 2.0 (the "License")
 *
 * 24-Feb-2017	Brendan Gregg	Created this.
 */

var click = 0;		// click count
var min_y = 999999;	// min y dimension of heatmap
var max_y = 0;		// max y dimension of heatmap
var selected_x = 0;
var selected_y = 0;

var color_mouseover = "rgb(200,0,200)";		// box hover
var color_selected = "rgb(150,0,150)";		// box selected
var color_column = "rgb(200,0,200)";		// column highlight
var rect_selected;

function doflamegraph() {
	document.forms["rangeform"].submit();
}

function details2time(d, isstart) {
	// example string:
	// time 14s, range 342-361ms, count: 2, colpct: 0%
	var p = d.split(" ");
	var secs = p[1];
	var range = p[3];
	p = range.split("-");
	var ms;
	if (isstart) {
		ms = p[0];
	} else {
		ms = p[1];
	}
	var ret = parseInt(secs) + (parseInt(ms) / 1000);
	return (ret.toFixed(3));
}

function mouseover()
{
	// highlight box in magenta
	this.oldfill = this.getAttribute("fill");
	this.setAttribute("fill", color_mouseover);

	// add range highlighting
	var svg = document.getElementsByTagName('svg')[0];
	var x = parseInt(this.getAttribute("x"));
	var y = parseInt(this.getAttribute("y"));
	var h = parseInt(this.getAttribute("height"));
	var w = parseInt(this.getAttribute("width"));

	if (click == 0 || (x > selected_x)) {
		hx = x; hy = y;
		if (click != 0) {
			hx = selected_x;
			hy = selected_y;
		}
		var rect1 = document.createElementNS("http://www.w3.org/2000/svg", "rect");
		rect1.setAttribute("x", hx.toString());
		rect1.setAttribute("width", this.getAttribute("width"));
		rect1.setAttribute("fill", color_selected);
		rect1.setAttribute("fill-opacity", "0.2");
		rect1.setAttribute("id", "highlight1");
		rect1.setAttribute("y", min_y.toString());
		rect1.setAttribute("height", (hy - min_y).toString());
		svg.appendChild(rect1);
	}

	if (click == 1) {
		var rect2 = document.createElementNS("http://www.w3.org/2000/svg", "rect");
		rect2.setAttribute("x", x.toString());
		rect2.setAttribute("width", this.getAttribute("width"));
		rect2.setAttribute("fill", color_selected);
		rect2.setAttribute("fill-opacity", "0.2");

		rect2.setAttribute("id", "highlight2");
		rect2.setAttribute("y", (y + h).toString());
		rect2.setAttribute("height", (max_y - y - h).toString());
		svg.appendChild(rect2);
		if (x == selected_x) {
			if (y < selected_y) {
				// recalculate height to span to start box
				rect2.setAttribute("height", (selected_y - y).toString());
			}
		} else if (x > (selected_x + w)) {
			var rect3 = document.createElementNS("http://www.w3.org/2000/svg", "rect");
			rect3.setAttribute("id", "highlight3");
			rect3.setAttribute("fill", color_selected);
			rect3.setAttribute("fill-opacity", "0.2");
			rect3.setAttribute("x", selected_x + w);
			rect3.setAttribute("y", min_y.toString());
			rect3.setAttribute("height", (max_y - min_y).toString());
			rect3.setAttribute("width", x - selected_x - w);
			svg.appendChild(rect3);
		}
	}

}

function clearhighlights()
{
	var highlight1 = document.getElementById("highlight1");
	var highlight2 = document.getElementById("highlight2");
	var highlight3 = document.getElementById("highlight3");
	var svg = document.getElementsByTagName('svg')[0];
	if (highlight1)
		svg.removeChild(highlight1);
	if (highlight2)
		svg.removeChild(highlight2);
	if (highlight3)
		svg.removeChild(highlight3);
}

function mouseout()
{
	this.setAttribute("fill", this.oldfill);
	clearhighlights();
}

function resetrange()
{
	click = 0;
	clearhighlights();
	if (rect_selected) {
		rect_selected.setAttribute("fill", rect_selected.reallyoldfill);
	}
}

function doclick(r)
{
	var d = document.getElementById("details");
	var start = document.getElementById("input_start");
	var end = document.getElementById("input_end");
	if (click == 0) {
		// fill in form
		start.value = details2time(d.innerHTML, 1);

		click++;
		clearhighlights();

		// set selected color
		this.setAttribute("fill", color_selected);
		this.reallyoldfill = this.oldfill;
		this.oldfill = color_selected;
		rect_selected = this;

		// fetch selected position for later highlighting
		var x = parseInt(this.getAttribute("x"));
		var y = parseInt(this.getAttribute("y"));
		selected_x = x;
		selected_y = y;
	} else {
		// fill in form
		end.value = details2time(d.innerHTML, 0);

		click = 0;
		if (parseFloat(end.value) < parseFloat(start.value)) {
			alert("Selected end time is before start. Try again");
			resetrange();
			return;
		}

		doflamegraph();
	}
}

function pageinit()
{
	// add click listeners to all heatmap rectangles
	// eg,
	// <rect x="242.0" y="196" width="8.0" height="8.0" fill="rgb(255,231,212)" onmouseover="s('29','608-627ms',2,391,429)" onmouseout="c()" />
	var rects = document.getElementsByTagName("rect");
	for (var i = 1; i < rects.length; i++) {
		var r = rects[i];
		// Match a heatmap box by checking its width. Yes, I could add
		// a class to these for perfect identification, but I prefer
		// not to inflate the heatmap size if it can be avoided.
		// This could, instead, check that 
		if (r.getAttribute("id") != "bkg") {
			r.addEventListener("click", doclick);
			r.addEventListener("mouseover", mouseover);
			r.addEventListener("mouseout", mouseout);

			// record max heatmap height for later highlighting
			var y = parseInt(r.getAttribute("y"));
			var h = parseInt(r.getAttribute("height"));
			if (y < min_y) {
				min_y = y;
			}
			if (y + h > max_y) {
				max_y = y + h;
			}
		}
	}
}
