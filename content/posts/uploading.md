+++
draft = false
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
[^0]

I have recently been working on a real time rendering project, and one of the problems I wanted to tackle was real time streaming of large amounts of data from disk to the GPU. Streaming can be particularly interesting when you have large scenes that don't entirely fit in RAM, or you want to minimize startup time by loading data over time instead of everything upfront. NVME drives are surprisingly fast and cheap nowadays, and the read bandwidth can easily be over 8 GB/s for a single disk, getting closer and closer to RAM and PCIe bandwidth, depending on the setup, thus allowing to really push the limits of how large of a scene can be streamed directly from disk.

But this post isn't really about disk. I might get into the details of I/O in a later post, but before that, I wanted to explore different options to upload from main memory to GPU memory. Modern rendering APIs expose multiple ways to move data, that, when available, map to different hardware and system features.  The main goal of this post is to describe some of the approaches that can be used, look at how they map to the underlying hardware and compare their performance.  Hopefully this can help making informed decisions when designing such a system.

## Setup

I am using Vulkan in this project, I'll try to be consistent and stick with its terminology, but most of what we'll be doing directly translates to all modern low-level graphics APIs (e.g. D3D12 and, I assume, Metal) and to other APIs that allow you to access the GPU like CUDA and ROCm.

The specific workload we are using as a benchmark is uploading a large mesh for rendering. In practice, this requires uploading a big buffer of vertices and launching draw commands. We are interested in the upload performance so we will keep the rest of the setup as simple as possible:

- Both vertex and fragment shaders are going to be very minimal, we only have positions as vertex attributes, we transform them in the vertex shader and apply Blinn-Phong shading with a fixed directional light in the fragment shader.
- We can assume the topology is fixed, e.g. we don't have to worry about streaming triangle indices.
- We only render color directly to the window buffer, no other copies or passes required.

This means every frame we really only have to upload one buffer containing vertices for the current frame, setup the pipeline state for the draw and kick off a single draw from the CPU.

Since we are dealing with memory movement it's important to see where and how we can allocate memory in different places and with different properties. We are now going to look at how Vulkan exposes this information.  If you are already familiar with Vulkan memory management or are you here just to see performance numbers you can skip the following two sections.

### Memory heaps
Vulkan exposes two main concepts when dealing with memory, memory heaps and memory types. Memory heaps are large regions of physical memory that we can use for allocating resources and that the Vulkan device (usually a GPU) can access when executing rendering, compute and transfer commands.

You can use [`vulkaninfo`](https://vulkan.lunarg.com/doc/view/latest/windows/vulkaninfo.html) to dump information about which heaps are available on your system. On my laptop with a dedicated Nvidia GPU its output look something like this:

```text
memoryHeaps[0]:
        size   = 6287261696 (0x176c00000) (5.86 GiB)
        budget = 5481955328 (0x146c00000) (5.11 GiB)
        usage  = 0 (0x00000000) (0.00 B)
        flags: count = 1
                MEMORY_HEAP_DEVICE_LOCAL_BIT
memoryHeaps[1]:
        size   = 8511975424 (0x1fb5a7000) (7.93 GiB)
        budget = 7817941197 (0x1d1fc50cd) (7.28 GiB)
        usage  = 0 (0x00000000) (0.00 B)
        flags:
                None
```
Heap 0 is dedicated GPU memory (we can tell because of the `MEMORY_HEAP_DEVICE_LOCAL_BIT`), this roughly matches the 6 GB of VRAM on my 3060 Mobile.

Heap 1 is CPU memory, Vulkan allows applications to allocate roughly up to half of the total 16 GB of physical memory in the system.

### Memory types

Memory types are what we pass Vulkan when asking for some memory, they contain the index of the heap on which the allocation happens and a set of flags. There is usually more than one memory type available per-heap, you can think of them as different parameters you can set when performing an allocation on that heap.

On my system the following memory types are available:
```text
memoryTypes: count = 6
        memoryTypes[0]:
                heapIndex     = 1
                propertyFlags = 0x0000:
                        None
                usable for:
                        IMAGE_TILING_OPTIMAL:
                        IMAGE_TILING_LINEAR:
                                color images
                                (non-sparse, non-transient)
        memoryTypes[1]:
                heapIndex     = 0
                propertyFlags = 0x0001: count = 1
                        MEMORY_PROPERTY_DEVICE_LOCAL_BIT
                usable for:
                        IMAGE_TILING_OPTIMAL:
                                color images
                                FORMAT_D16_UNORM
                                FORMAT_X8_D24_UNORM_PACK32
                                FORMAT_D32_SFLOAT
                                FORMAT_S8_UINT
                                FORMAT_D24_UNORM_S8_UINT
                                FORMAT_D32_SFLOAT_S8_UINT
                        IMAGE_TILING_LINEAR:
                                color images
                                (non-sparse, non-transient)
        memoryTypes[2]:
                heapIndex     = 0
                propertyFlags = 0x0001: count = 1
                        MEMORY_PROPERTY_DEVICE_LOCAL_BIT
                usable for:
                        IMAGE_TILING_OPTIMAL:
                                None
                        IMAGE_TILING_LINEAR:
                                None
        memoryTypes[3]:
                heapIndex     = 1
                propertyFlags = 0x0006: count = 2
                        MEMORY_PROPERTY_HOST_VISIBLE_BIT
                        MEMORY_PROPERTY_HOST_COHERENT_BIT
                usable for:
                        IMAGE_TILING_OPTIMAL:
                                None
                        IMAGE_TILING_LINEAR:
                                color images
                                (non-sparse, non-transient)
        memoryTypes[4]:
                heapIndex     = 1
                propertyFlags = 0x000e: count = 3
                        MEMORY_PROPERTY_HOST_VISIBLE_BIT
                        MEMORY_PROPERTY_HOST_COHERENT_BIT
                        MEMORY_PROPERTY_HOST_CACHED_BIT
                usable for:
                        IMAGE_TILING_OPTIMAL:
                                None
                        IMAGE_TILING_LINEAR:
                                color images
                                (non-sparse, non-transient)
        memoryTypes[5]:
                heapIndex     = 0
                propertyFlags = 0x0007: count = 3
                        MEMORY_PROPERTY_DEVICE_LOCAL_BIT
                        MEMORY_PROPERTY_HOST_VISIBLE_BIT
                        MEMORY_PROPERTY_HOST_COHERENT_BIT
                usable for:
                        IMAGE_TILING_OPTIMAL:
                                None
                        IMAGE_TILING_LINEAR:
                                color images
                                (non-sparse, non-transient)
```

As we can see from the output each memory type has 3 important pieces of information:
- `heapIndex`: on which heap is this memory allocated.
- `propertyFlags`: some flags describing properties of the memory.
- `usable for`: a list of types of resources that can be allocated using this memory type.

Let's break down different memory types and their uses by heap type.

CPU memory (Heap 1) has 3 memory types:
- Type 4 is normal CPU memory, similar to what you would get from `VirtualAlloc` or `mmap`, this memory can be mapped (`MEMORY_PROPERTY_HOST_VISIBLE_BIT`), is cache coherent (`MEMORY_PROPERTY_HOST_VISIBLE_BIT`) with the GPU (meaning we don't need to call [`vkFlushMappedMemoryRanges`](https://registry.khronos.org/vulkan/specs/latest/man/html/vkFlushMappedMemoryRanges.html) on it) and is also cached (`MEMORY_PROPERTY_HOST_CACHED`) on the CPU.
- Type 3 is the same as type 4 but without the `MEMORY_PROPERTY_HOST_CACHED_BIT`.  This is write-combining memory, it can be useful for streaming data to memory without polluting caches. Since this memory is not cached, reads to it can be [surprisingly slow](https://fgiesen.wordpress.com/2013/01/29/write-combining-is-not-your-friend) and it should only be used for writing by the CPU and reading from the GPU.
 - Type 0 is neither device local, nor it can be mapped on the CPU, other than preventing the program to map this memory by mistake, I am not really sure if this has any practical use.

GPU memory (Heap 0) has 3 memory types. All those types have the `MEMORY_PROPERTY_DEVICE_LOCAL_BIT` set, specifying that this is memory that is fast to access from the GPU:
- Type 1 has usable flags that show a bunch of image types that can be stored in this type of memory in `IMAGE_TILING_OPTIMAL` and `IMAGE_TILING_LINEAR` tiling.  [Image tiling](https://registry.khronos.org/vulkan/specs/latest/man/html/VkImageTiling.html) controls the arrangement of pixels in memory, the layout is important for read / write performance.
- Type 2 is the same as type one, but it does not support images and can only be used for buffers.
- Type 5 also can't be used for images, but it has the interesting property of having the `MEMORY_PROPERTY_HOST_VISIBLE_BIT` which means that we can map this memory from the CPU and read or write to it.  This type of memory exposes access to GPU memory directly from the CPU through [PCIe BAR](https://stackoverflow.com/questions/30190050/what-is-the-base-address-register-bar-in-pcie).  On older systems this was limited to 256MB of RAM, but on systems with [Resizable BAR](https://www.nvidia.com/en-us/geforce/news/geforce-rtx-30-series-resizable-bar-support/), as this one, the entire GPU memory heap can be accessed this way. This memory is also write-combining, this allows individual memory writes to be batched into bigger transactions that are sent as packets on the PCIe bus.


## Upload strategies

Now that we have seen what types of memory are available we can plan some strategies to stream in and render our scene. All following snippets are really just pseudo code to sketch out the shape of the code  for each approach. In practice there are some big gaps to fill, like filling and passing big structs of arguments to make Vulkan happy, and checking for the actual memory type and resources available on the system instead of using hard-coded constants.

Our main rendering loop looks something like this:

```c++
u64 vertices_count = ...;   // Number of vertices we want to render
vec3* vertices = ...;       // Pre-allocated buffer for vertices

//
// <TODO: put setup code here>
//
while (!quit) {
    // Acquire next image buffer for rendering to the window
    ...
    vkAcquireNextImageKHR(...);
    ...

    // Populate array of vertices for this frame (application specific)
    GetVerticesForThisFrame(vertices, vertices_count, ...);

    //
    // <TODO: put upload code here>
    //

    // Configure the pipeline for rendering
    ...
    vkCmdBindPipeline(...)
    vkCmdBindVertexBuffers(...)
    ...

    // Dispatch draw
    vkCmdDrawIndexed(...)

    // Submit commands and present
    ..
    vkQueueSubmit(...);
    vkQueuePresntKHR(...);
    ...

    frame_index = (frame_index + 1) % FRAMES_IN_FLIGHT;
}
```

We will compare 5 different strategies of (somewhat) increasing levels of complexity and (hopefully) performance.

### 1. CPU memory

The easiest thing we can do is to not upload data at all. We can allocate the vertices in CPU cached memory (memory type 4), treat is as normal memory on the CPU and just ask the GPU to fetch the vertices from CPU memory when drawing:
```c++
// setup:
VkBuffer buffers[FRAMES_IN_FLIGHT];
span<u8> buffer_maps[FRAMES_IN_FLIGHT]; // span is just a pointer + size
for(int i = 0; i < FRAMES_IN_FLIGHT; i++) {
    // This helper takes care of creating a buffer, allocating memory of a
    // specific type, binding the memory to the buffer and mapping it.
    CreateAndMapBuffer(&buffers[i], &buffer_maps[i],
        /* size=*/ sizeof(vec3) * vertices_count, /* memory type=*/ 4);
}

while(!quit) {
    ...

    // upload: (not actually uploading anything, just copying to the buffer)
    memcpy(map.data, vertices, sizeof(vec3) * vertices_count);


    ...
}
```
We can even skip the `memcpy` if we instead directly get the vertices, for the current frame in the respective mapped buffer.  We'll use the version without extra copies later in the performance section.


### 2. PCIe BAR

The second easiest thing to do is to write the buffers directly to the GPU making use of PCIe BAR memory (memory type 5).

```c++
// setup:
...
for(int i = 0; i < FRAMES_IN_FLIGHT; i++) {
    // Same as above, only difference is the memory type
    CreateAndMapBuffer(&buffers[i], &buffer_maps[i],
        /* size=*/ sizeof(vec3) * vertices_count, /* memory type=*/ 5);
}
...
```

We have to be careful using the buffer here, as it is mapped as write-combining memory, we need to make sure we are writing sequentially and never reading from this buffer, for a fair comparison we will leave the `memcpy` in this case, assuming in a more general setup vertices are generated in normal memory first and then copied to it.

### 3. Synchronous copy
Instead of having the CPU do the copy, we can let the GPU handle this for us using [`vkCmdCopyBuffer`](https://registry.khronos.org/vulkan/specs/latest/man/html/vkCmdCopyBuffer.html). For this we need to allocate two buffers for each frame, a staging buffer on the CPU (memory type 4) and the actual buffer used for rendering on the GPU (memory type 2), and each frame we will then trigger a copy from CPU to GPU.

```c++
// setup:
VkBuffer cpu_buffers[FRAMES_IN_FLIGHT];
span<u8> cpu_buffer_maps[FRAMES_IN_FLIGHT];

VkBuffer gpu_buffers[FRAMES_IN_FLIGHT];

for(int i = 0; i < FRAMES_IN_FLIGHT; i++) {
    // Allocate a buffer in normal CPU memory
    CreateAndMapBuffer(&cpu_buffers[i], &cpu_buffer_maps[i],
        /* size=*/ sizeof(vec3) * vertices_count, /* memory type=*/ 4);

    // Allocate a buffer of the same size in GPU memory (CPU does not need to map this)
    CreateBuffer(&gpu_buffers[i],
        /* size=*/ sizeof(vec3) * vertices_count, /* memory type=*/ 2);
}

while(!quit) {
    ...

    // Populate array of vertices for this frame directly into cpu buffer (app specific)
    GetVerticesForThisFrame(cpu_buffer_maps[frame_index].data, vertices_count);

    ...

    // upload:
    vkCmdCopyBuffer(..., cpu_buffers[frame_index], gpu_buffers[frame_index], ...)

    // A memory barrier is needed to synchronize transfer and vertex buffer usage
    VkMemoryBarrier2KHR memoryBarrier = {
        ...
        .srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT,
        .srcAccessMask = VK_ACCESS_2_MEMORY_WRITE_BIT,
        .dstStageMask = VK_PIPELINE_STAGE_2_VERTEX_ATTRIBUTE_INPUT_BIT,
        .dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT,
    };
    vkCmdPipelineBarrier2KHR(...)

    ...
}
```
We now need a barrier to flush / invalidate caches and ensure drawing does not start before copying is done, because in Vulkan commands are allowed to be reordered even within the same command buffer. Before, when doing the copy directly on the CPU, the synchronization between CPU and GPU was implied by the command submission in `vkQueueSubmit`.

There are two main advantages with this approach, first we can free up CPU cycles, additionally PCIe BAR writes from the previous approach usually do not saturate the bandwidth of the PCIe bus, whereas the GPU can potentially achieve higher transfer throughput, we'll pick this topic up again in the performance section.

### 4. Asynchronous copy on compute queue

Here is where things get interesting, in the synchronous copy case we are executing both the copy and draw command on the same command queue, but a Vulkan device can expose multiple queues that can execute independent streams of commands concurrently. The idea here is to use two queues to overlap the streaming of data and rendering. Within a frame we need rendering to wait for the upload to happen, but we can start uploading the buffer for the next frame while we are rendering the current one.

Setup remains similar to the synchronous case, with the additional step of allocating a second queue and a few pairs of semaphores.  The main loop is now a bit more involved, as we need to deal with synchronization across multiple queues.

```c++
// setup
...
VkQueue async_compute_queue;
vkGetDeviceQueue(device, async_compute_queue_family_index, 0, &async_compute_queue);
// ... allocate command pools and command buffers for this queue ...
...


VkSemaphore copy_done_semaphores[2];    // used by the transfer queue to signal the copy is done
VkSemaphore render_done_semaphores[2];  // used by the graphics queue to signal rendering is done
for(int i = 0; i < FRAMES_IN_FLIGHT; i++) {
    VkSemaphoreCreateInfo semaphore_info = { VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO };
    vkCreateSemaphore(device, &semaphore_info, 0, copy_done_semaphores[i]);
    vkCreateSemaphore(device, &semaphore_info, 0, render_done_semaphores[i]);
}

while(!quit) {
    ...

    // upload:
    {
        // Async copy
        vkCmdCopyBuffer(async_cmd_buffer, cpu_buffers[frame_index], gpu_buffers[frame_index], ...)

        // Memory barrier on the async queue
        VkBufferMemoryBarrier2KHR bufferMemoryBarrier = {
            .buffer = gpu_buffers[frame_index],
            .srcQueueFamilyIndex = async_compute_queue_family_index,
            .dstQueueFamilyIndex = graphics_queue_family_index,
            .srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT,
            .srcAccessMask = VK_ACCESS_2_MEMORY_WRITE_BIT,
        };
        vkCmdPipelineBarrier2KHR(async_cmd_buffer, ...)

        // Submit commands on the async queue
        VkPipelineStageFlags stage_mask = VK_PIPELINE_STAGE_TRANSFER_BIT;
        VkSubmitInfo submit_info = {
            .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
            .commandBufferCount = 1,
            .pCommandBuffers = &async_cmd_buffer,
            // The semaphore is initialized in non-signaled state, but
            // the first few frames do not need to wait for rendering to be done.
            .waitSemaphoreCount = total_frame_index >= FRAMES_IN_FLIGHT ? 1 : 0,
            .pWaitSemaphores = &render_done_semaphores[frame_index],
            .pWaitDstStageMask = &stage_mask,
            .signalSemaphoreCount = 1,
            .pSignalSemaphores = &copy_done_semaphores[frame_index],
        }
        vkQueueSubmit(async_queue, 1, &submit_info, VK_NULL_HANDLE);
    }

    // Memory barrier on the graphics queue
    VkBufferMemoryBarrier2KHR bufferMemoryBarrier = {
        .buffer = gpu_buffers[frame_index],
        .srcQueueFamilyIndex = async_compute_queue_family_index,
        .dstQueueFamilyIndex = graphics_queue_family_index,
        .dstStageMask = VK_PIPELINE_STAGE_2_VERTEX_ATTRIBUTE_INPUT_BIT,
        .dstAccessMask = VK_ACCESS_2_VERTEX_ATTRIBUTE_READ_BIT,
    };
    vkCmdPipelineBarrier2KHR(graphics_cmd_buffer, ...)


    // Bind pipeline and draw on the graphics queue (same as above)
    ...


    // Submit commands on the graphics queue and present
    VkSemaphore wait_semaphores[] = {
        frame_acquire_semaphore, // Standard frame acquire semaphore (omitted above for brevity)
        copy_done_semaphores[frame_index],
    };

    VkPipelineStageFlags wait_stages[] = {
        VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, // Waiting for backbuffer to be ready
        VK_PIPELINE_STAGE_VERTEX_INPUT_BIT,            // Waiting for vertices to be ready
    };

    VkSemaphore signal_semaphores[] = {
        frame_release_semaphore, // Standard frame release semaphore (omitted above for brevity)
        render_done_semaphores[frame_index],
    };

    VkSubmitInfo submit_info = {
        .sType VK_STRUCTURE_TYPE_SUBMIT_INFO
        .commandBufferCount = 1;
        .pCommandBuffers = &graphics_cmd_buffer;
        .waitSemaphoreCount = 2;
        .pWaitSemaphores = wait_semaphores,
        .pWaitDstStageMask = wait_stages;
        .signalSemaphoreCount = 2;
        .pSignalSemaphores = signal_semaphores;
    };
    vkr = vkQueueSubmit(graphics_queue, 1, &submit_info, frame_fence);

    ...
    frame_index = (frame_index + 1) % FRAMES_IN_FLIGHT;
    total_frame_index += 1;
}
```
When dealing with commands on different queues we need to explicitly handle execution serialization (with semaphores), memory synchronization and resource transfer between queues (with buffer memory barriers). The `copy_done_semaphores` are signaled on the copy queue when a copy is done and waited by the graphics queue before using the buffer for rendering. In the opposite direction, `render_done_buffers` semaphores are signaled on the graphics queue when drawing is done and the next copy can start. We use a semaphore per frame, this way the copy for the next frame can start before the drawing of the current frame is finished.

We also have two barriers now instead of one, this is because Vulkan mandates the need of a barrier on each queue when transferring a resource across queues, but the goal is the same as before, flush caches after writing, and invalidate caches before reading for rendering.


### 5. Async copy on transfer queue

There is a third type of queue, in addition to the graphics and the async compute queue: the transfer queue. This queue's whole purpose is to transfer data between CPU and GPU memory. To use it, we only need to change the index of the async queue.

```c++
VkQueue transfer_queue;
vkGetDeviceQueue(device, transfer_queue_family_index, 0, &transfer_queue);

// Everything else same as async compute
...

```

Modern GPUs have a dedicated piece of hardware that can trigger DMA (Direct Memory Access) operations for transferring data over the PCIe bus. NVIDIA calls this ["Asynchronous Copy Engine"](https://developer.nvidia.com/blog/advanced-api-performance-async-copy/). The Vulkan transfer queue on this GPU allows us to take advantage of this feature, and, as we will see in the next section, using it can have a significant impact on performance.

## Performance

We will now compare the performance of the five methods.  All the performance numbers and captures are from my laptop with an Nvidia RTX 3060 Mobile, an i5 11400H and 16GB of single channel DDR4 at 3200MHz. The GPU is on a PCIe Gen 3 link with 16 lanes, with a theoretical throughput of 32GB/s. Both the CPU and the GPU are set to the fastest power state with boost clock enabled. I plan to run the same benchmark also on a desktop and maybe on integrated GPU in the future, but my laptop is all I have around at the moment.

The test scene is a mesh with 2.8M vertices and 5M triangles. Index data is static and uploaded only once to the GPU, so we are only interested in vertex data. Our vertices are 3D positions stored as single precision floats, resulting in approximately 32MB uploaded each frame. Presentation mode is set to `VK_PRESENT_MODE_IMMEDIATE_KHR` (e.g. vsync is off) to ensure the framerate is not limited by the monitor refresh rate.


Here are average frame times for each method and achieved upload bandwidth (upload size divided by frame time):

| Method           | Frame time (ms)  | Upload bandwidth (GB/s) |
|------------------|------------------|-------------------------|
| CPU memory       |      4.39        | 7.46                    |
| PCIe BAR         |      3.95        | 8.30                    |
| Synchronous      |      3.05        | 10.74                   |
| Async compute    |      2.32        | 14.12                   |
| Async transfer   |      1.58        | 20.73                   |


### Execution traces

I also collected GPU traces using Nsight to analyze the performance of each method and visualize timelines of how transfer overlaps with drawing.

Frame times are overall a bit higher during a capture and frame timings are a bit more unstable, only for the PCIe BAR method the timings are still similar, I think this might be because this method is the only one that is not GPU bound in terms of performance, therefore the overhead of profiling does not affect it as much.

For those not familiar with the tool, the screenshots below show the timeline of the GPU execution for a few frames and a few performance metrics. Time flows left to right for a few frames, and the rows describe the following:
- **Frames**: frame presentation boundaries
- **GPU Contexts**: what process is currently using the GPU, in our examples it's mostly our Vulkan application, but you can find a few spots here and there in which `dwm.exe` is taking control (this is the Windows compositor, the process that renders the desktop and windows).
- **Vulkan Graphics/Compute/Transfer Q**: Execution of Vulkan commands on a queue, this includes synchronization markers, and ranges that show how long a different command or operation takes. In the "Actions" row we can see draw commands in blue and copy commands in green.
- **GPU engine activity**: usage of a specific part of the GPU, this include the graphics engine (yellow) copy engine (pink), I think during an Nsight capture the copy engine is always running to transfer metrics from the GPU to the CPU, so this line is always at 100% and it's not that interesting.
- **PCIe bandwidth**: the lighter color is RX (CPU to GPU) and the the darker is TX (GPU to CPU) bandwidth in percentage of the 32 GB/s theoretical max. TX bandwidth is always at ~5 GB/s (17% of max) during recording of traces, I assume because it's used to transfer counters and metrics collected during profiling back to the CPU.
- **PCIe Incoming BAR accesses**: number of read and write requests to BARs on the PCIe bus to the GPU.
- **SM warp occupancy**: statistics on how many threads are running on GPU cores, this is correlated with GPU usage (but not exactly the same thing).

#### CPU memory
![Nsight screenshot of CPU memory method](host.png)

Here the GPU is loading vertices from CPU memory during rendering. The timeline shows that only operation happening on the GPU is drawing (blue boxes in the Actions row) and the framerate depends exclusively on the time it takes to render.

Vertices are loaded on demand and have to go through the PCIe bus slowing down vertex shader execution significantly. The 32MB of vertices don't fit in the 3MB L2 cache of the RTX 3060 Mobile, therefore vertices are potentially moved over the bus multiple times depending on the locality of indices (I don't think vertex order is particularly optimized for locality in this mesh, but it's not completely random access either). We also see that the achieved PCIe bandwidth is far from optimal (generally around 20% with some spikes at 40%).

Using CPU memory directly can be useful for smaller buffers that can fit in GPU caches (e.g. for smaller constants and parameters), but for large buffers like this it's definitely not the best approach in terms of performance. The only upside of this method is that it doesn't consume GPU memory, which can be useful if GPU memory is limited.

#### PCIe BAR
![Nsight screenshot of PCIe BAR method](bar.png)

The first thing we notice is that rendering from GPU memory is ~3.5x faster (~1.7ms vs ~6ms). But the frame time is not as fast because we have empty gaps between draws. During this time the CPU is busy writing data to the BAR and the GPU is waiting for commands. The line at the bottom shows PCIe BAR writes by the CPU, we can see that those are saturated almost all the time, but the PCIe bandwidth is at around 38%, this is because BAR writes can't fully utilize the PCIe link speed.

#### Synchronous
![Nsight screenshot of Synchronous method](sync.png)

Here we are doing the copy with `vkCmdCopyBuffer` (green boxes in the Actions row) on the same queue we are using for rendering. Drawing time is similar to the above method because vertices are also on the GPU, but the copy is performed by the GPU instead. Drawing and copying are sequential and there is no overlap between frames, we can also see in the PCIe bandwidth row that the link is usage is high during copies (~60% of max) but zero during draws. In theory the Vulkan spec allows the device to run commands on the same queue concurrently, even if submitted in separate calls to `vkQueueSubmit`, but the driver is clearly not doing any of that here.

It's also interesting to note that the SM Warp occupancy row shows some compute shaders running (orange lines) during copies. This is because the buffer copy is being performed by a compute shader that is reading from CPU RAM and writing to GPU RAM. The compute shader approach clearly achieves higher bandwidth than the BAR writes from the CPU, but it's also using GPU resources that could potentially be used for other workloads. This is not an issue here because copying is all we are doing at this time, but in a more complex application there might be better uses for asynchronous computation.


#### Async compute
![Nisght screenshot of Async compute method](compute.png)

In this trace we now have two queues, one for the copy one for drawing. Similarly to the synchronous case, copies are happening in a compute shader, but we can clearly see that they are overlapping with draws. The frame time is much faster and is now dominated exclusively by copying time, but we are still not close (~60%) to maxing out the PCIe link when copying from a compute shader. The same downsides of the synchronous method of using compute units to copy still stand though.

#### Async transfer
![Nisght screenshot of Async compute method](copy.png)

Finally we get to the transfer queue. Scheduling is also on two queues and overlapping, but there are no compute shaders running (no orange lines in the bottom raw) as the copies are running on the copy engine. PCIe bandwidth usage is much higher during copies (80-90%) and frame time is now dominated by draw time instead. This method is the fastest and most efficient, as it keeps both the CPU and GPU free during copying. As far as I know there is no downside to using the transfer queue. If it's available, of course.


## Conclusion

In this benchmarked the transfer queue is the clear winner, it requires a bit of complexity to setup the required synchronization across queues, but it's by far the most efficient method. If it's not available, async compute or synchronous copy can achieve higher bandwidth by using the GPU for copies, but they might not be optimal if you have other workloads running on the GPU. If BAR memory is available it can also be a good choice, if you have free CPU cycles to spare, but otherwise it does not achieve great bandwidth with large transfers, on the other hand it is probably the method with the lowest latency for smaller buffers. Rendering directly from CPU memory should in general be avoided, and usually only considered for scenarios heavily constrained in terms of GPU memory.

It's also important to note that the workload we are benchmarking is much simpler than a real application, transfer and rendering speed can be affected by other operations running concurrently, or there might be other bottlenecks related to usage of GPU compute units or memory bandwidth.

Furthermore, in real applications, the rate of streaming and rendering might not be one to one, you could be uploading at data rate (e.g. 30fps) but rendering at the monitor refresh rate (e.g. 60-144fps), in this case the synchronization and resource management can become more complex, but copying could be less of a bottleneck. For smoother presentation you also might want to worry about [frame pacing](https://raphlinus.github.io/ui/graphics/gpu/2021/10/22/swapchain-frame-pacing.html). I plan to go further into these topics in a later post.

Finally it's important to remember that we looked only at one specific hardware setup, a laptop with a fairly recent discrete GPU, the performance characteristics of desktop, integrated graphics or mobile GPUs might vary. I also plan to run this benchmark on a few different systems to check if the conclusions still hold, but in general there is no substitute for profiling your specifc workload on the target hardware.


[^0]: Title is a reference to [this post](https://fgiesen.wordpress.com/2018/02/19/reading-bits-in-far-too-many-ways-part-1/), the entire blog is amazing, and one of the main inspirations for starting my own in the first place.