+++
draft = true
date = 2025-02-09T20:16:17+01:00
title = "Uploading data to the GPU in far too many ways"
description = ""
slug = ""
authors = []
tags = []
categories = []
externalLink = ""
series = []
home = true
+++

## The goal

I have recently been working on a real time rendering project, and one of the
problems I wanted to tackle was real time streaming of large amounts of data
from disk to the GPU. Streaming can be particularly interesting when you have
large scenes that don't really fit in RAM, or you want to minimize startup time
by loading data over time instead of everything upfront. NVME drives are
surprisingly fast and cheap nowadays, and the read bandwidth can easily be over
8 GB/s for a single disk, getting closer and closer to RAM and PCIe bandwidth,
depending on the setup, thus allowing to really push the limits of how large
of a scene can be streamed directly from disk.

But this post isn't really about disk. I might get into the details of I/O in a
later post, but before that, I wanted to explore different options to upload
from main memory to GPU memory. Modern rendering APIs expose multiple ways to
move data, that, when available, map to different hardware and system features.
The main goal of this post is to describe some of the approaches I tried, look
at how they map to the underlying hardware and compare their performance.
Hopefully this can help make informed decisions when designing such a system.

## The setup

I am using Vulkan in this project, I'll try to be consistent and stick with its
terminology, but most of what we'll be doing directly translates to other
modern low-level APIs (e.g. D3D12 and, I assume, Metal) and to other APIs that
allow you to access the GPU like CUDA and ROCm.

The specific workload we are using as a benchmark is uploading a large mesh
for rendering, in practice this requires uploading a big buffer of vertices
and triggering draw commands. We are interested in the upload performance
so we will keep the rest of the setup as simple as possible:

- We can assume the topology is fixed, e.g. we don't have to worry about
streaming triangle indices.
- Both vertex and fragment shaders are going to be
very minimal, we only have positions as vertex attributes, we transform them in
the vertex shader and apply blinn-phong shading with a fixed directional light
in the fragment shader.
- We only render color directly to the window buffer,
no other copies or passes required.

This means every frame we really only have to upload one buffer containing
vertices for the current frame, setup the pipeline state for the draw and kick
off a single draw from the CPU.

## The approaches
Vulkan exposes multiple memory types
<> speak about differnt memory types (cite sawicki)
<> describe my setup, look at heaps / memory types

## Performance