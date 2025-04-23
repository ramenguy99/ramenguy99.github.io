+++
draft = false
date = 2025-03-27T21:22:23+01:00
title = "Notes on GPU synchronization in graphics and compute APIs"
description = ""
slug = ""
authors = []
tags = []
categories = []
externalLink = ""
series = []
home = true
+++

Modern graphics and compute APIs require a good deal of manual work to ensure that operations running concurrently on the CPU and the GPU are properly sequenced. Concurrent execution and resource sharing is very important for performance, making sure everything works correctly is not an easy task, this is usually referred under the broad term "synchronization".

There is a lot of good material out there on GPU synchronization, but most of it is usually focusing on one specific API or comparing two. It's easy to forget a few details or quirks about a specific API, especially when coming back to it after a long time, my hope is that this post can be useful to give an overview of the problem and the API space, and as a reference for readers (and for future me).

## Why is synchronization needed, anyway?

To understand where the need for synchronization comes from I think it's good to have a mental model of how the underlying hardware and software systems work. Even if the model is simpler than the real system and not always accurate, it can help build some intuition and provide good first answers to questions that arise while designing programs that deal with manual synchronization.

There three main problems that synchronization needs to solve:

#### Execution ordering

The first problem solved by synchronization is correctly specifying the order of operations. GPUs are massively parallel processors, operations scheduled on them can be executed in parallel and potentially in different order depending on what resources are available and when commands are being picked up for execution. Additionally, the CPU and the GPU are usually operating on different timelines, the CPU is often doing things like issuing commands to the GPU, uploading data and reading it back, all of this while the GPU might be rendering, transferring data or displaying a frame to the screen. If operations have dependencies between them (and they most often do) we obviously need to ensure that previous operations are done before starting the ones that depend on them.

#### Memory ordering and cache coherency

The second, usually more subtle, problem that synchronization aims to solve has to deal with memory ordering and caches. In an ideal system, after a write operation is complete, any subsequent read operation will see the updated value. Unfortunately for programmers, real systems have way less strict rules regarding ordering, there are multiple reasons for this, most of them are in some way related to performance and power efficiency.

Practically speaking, depending on the system, we have potentially multiple layers of caches on both the CPU and the GPU. If a previously written value is held in a cache and we want to read it from some part of the system that has no access to this cache, we have to first flush the cache that holds it (this is the "make available" part of synchronization in Vulkan jargon). On the other side, if we want to read a new value from memory but we have a stale version of it in a cache, we need to first invalidate this cache ("make visible" in Vulkan terms).

Cache flushes and invalidations are needed for operations running entirely on the GPU as well as for operations that deal with memory transfers between the CPU and the GPU. Some systems have some default visibility guarantees that are handled at the hardware level, on those systems some of the requirements are lifted and we can rely on the hardware to perform cache updates for us. The most common example of this is that on modern x86 machines there is usually no need to explicitly flush or invalidate CPU caches when synchronizing with GPU operations to main memory through the PCIe bus. The memory controller handling PCIe traffic can read and write data directly to CPU caches. Such a system is said to be cache coherent.


#### Image layouts and transitions

There is also third topic that is usually bundled with synchronization, this has to do with image layouts. I won't go into the details here, again there is a lot of information available online on the topic. The short version of it is that, for efficiency, images are often stored with hardware specific pixel arrangements in memory. Image layouts can be changed at runtime to be more optimal for specific operations, these changes are commonly referred to as layout transitions.

This is nothing new, operations that read and write data (as layout transitions do) need to be properly synchronized with operations that they depend on and that depend on them. Low level GPU APIs usually combine synchronization commands and layout transitions into a single command for convenience, as such, they are usually discussed together.

## Synchronization primitives

Graphics and compute APIs have their specific semantics and  provide various mechanisms to specify synchronization operations.  Some APIs are more verbose and some APIs are implicitly doing a lot more by keeping track of resource states and the needed operations, freeing applications from doing most of the heavy lifting.

In this section we go through the graphics APIs from the simpler to the more complex in terms of synchronization, and at last discuss how the CUDA compute API compares with those (spoiler alert: it's way simpler).

#### D3D11 
D3D11 requires the least amount of synchronization of all APIs that we will see, here the driver is definitely doing a bunch of work for us, there are still a few cases where the application needs to take manual actions though:

- **Commands:** There are no explicit command buffers or queues, commands are implicitly recorded and submitted based on the state of the `ID311DeviceContext` object when draw and dispatch calls are made.
    - **Reordering within a queue:** no queues.
    - **Reordering across queues:** no queues.
- **GPU / GPU:**
    - **Execution ordering:** no explicit barriers, semaphores or fences.  Inserted by the driver, if needed.
    - **Memory ordering:** no explicit barriers. Inserted by the driver, if needed.
    - **Layout transition:** no explicit layout transitions. Inserted by the driver, if needed.
- **CPU / GPU:**
    - **Execution ordering:** `ID3D11Fence` exists, but is only useful for interoperability with other APIs such as D3D12.
    - **Memory ordering:** CPU uploads and readbacks are marked by explicit map and unmap operations. The driver has an opportunity to invalidate caches when a buffer is mapped for reading, and flush caches when a buffer is unmapped after writing. No persistently mapped buffers are available.

#### OpenGL
On the list, OpenGL is definitely closer to D3D11 than to other APIs, but it's important to note that the it has evolved a lot over the years, and modern versions (4.4+) and extensions provide a lot of low level features that were not available at first.
- **Commands:** There are no explicit command buffers or queues, commands are implicitly recorded and submitted based on the state of global GL context when draw and dispatch calls are made.
    - **Reordering within a queue:** no queues.
    - **Reordering across queues:** no queues.
- **GPU / GPU:**
    - **Execution ordering:** no explicit barriers, semaphores or fences.  Inserted by the driver, if needed.
    - **Memory ordering:** `glMemoryBarrier` must be used to synchronize dependencies with writes in compute shaders. However, it's hard to find precise information on when exactly those are mandatory.
    - **Layout transition:** no explicit layout transitions. Inserted by the driver, if needed.
- **CPU / GPU:**
    - **Execution ordering:** `GLsync` objects can be used to wait for GPU operations to complete. These are boolean objects that are either in signaled or unsignaled state.
    - **Memory ordering:** Transient map and unmap operations similar to D3D11 are available with `glMapBuffer[Range]`. OpenGL 4.4+ also supports persistently mapped buffers with `GL_MAP_PERSISTENT_BIT`. For those, synchronization is left to the applications with `GLsync` objects and if the mapping does not use the `GL_MAP_COHERENT_BIT` flag, caches must be manually flushed with `glFlushMappedBufferRange`.

#### D3D12
D3D12 is in the low level category, most of the synchronization is delegated to the application, it also introduces new concepts such as queues, command lists and many synchronization primitives.
- **Commands:** Commands are recorded on command lists (`ID3D11CommandList`), and lists are submitted to command queues (`ID3D12CommandQueue`). Multiple command list can be submitted to the same queue in a single frame, either with a single call to `ExecuteCommandLists` or with multiple calls.
    - **Reordering within a queue:** Subsequent calls to `ExecuteCommandLists` wait for the previous set of commands to finish. Commands from multiple command lists submitted in the same call can be interleaved. Barriers are needed to synchronize operations within a queue.
    - **Reordering across queues:** Different queues operate completely asynchronously. Fences (`ID3D12Fence`) can be used to synchronize across queues, they are 64 bit counters that can be incremented to signal that certain operations have completed and waited on until they reach a certain value.
- **GPU / GPU:**
    - **Execution ordering:** Resource barriers within a queue, fences across queues.  Fences are signaled and waited directly on a queue, instead of from a command. This means that it's not possible to signal or wait within a command list, but only before one is started or after one has fully executed.
    - **Memory ordering:** There are 3 types of resource barriers, all 3 types imply some sort of memory ordering.  UAV barriers flush and invalidate caches for UAV resources (resources with random read/write access from shaders).  Transition barriers must operate on a resource and, in addition, handle layout transitions.  Aliasing Barriers can be used to initialize resources that are backed by memory that is shared between multiple resources, I assume this allows to throw away previous contents, e.g.  discarding caches instead of flushing them. Additionally there are [implicit promotion and decay rules](https://learn.microsoft.com/en-us/windows/win32/direct3d12/using-resource-barriers-to-synchronize-resource-states-in-direct3d-12).  My understanding is that these allow the driver to omit certain barriers, if some operations do not require additional synchronization or are already synchronized by the driver. Split barriers are available through flags.
    - **Layout transition:** The API does not have an explicit concept for image layouts. But those are likely inferred from the `D3D12_RESOURCE_STATE` parameter of transition barriers. States are probably playing a combined role of specifying which part of the pipeline is using the resource and what it's layout should be, in a way the specify both stage and access at once, making life simpler for the application. 
- **CPU / GPU:**
    - **Execution ordering:** Fences can also be waited on and signaled by the CPU, providing a single interface for both GPU/GPU and CPU/GPU synchronization.
    - **Memory ordering:** Both transient and persistent mappings are possible.  Persistent mappings do not require explicit cache flushes, which means that coherent memory is implied, or memory ordering must explicitly be enforced for all potentially usable mapped resources by the driver at submission time.

#### Vulkan
Vulkan also requires explicit synchronization for most operations, its synchronization model is very fine grain and extremely verbose, making it by far the hardest API to use correctly.
- **Commands:** Commands are recorded on command buffers (`VkCommandBuffer`), and buffers are submitted to queues (`VkQueue`). Multiple command buffers can be submitted to the same queue in a single frame, either with a single call to `VkQueueSubmit` or with multiple calls. 
    - **Reordering within a queue:** Commands have some [implicit ordering guarantees](https://registry.khronos.org/vulkan/specs/latest/html/vkspec.html#synchronization-implicit) that depend on submission order make the API usable but are not much stronger than that. In general most commands with dependencies have to be manually synchronized.
    - **Reordering across queues:** Different queues operate completely asynchronously. Semaphores (`VkSemaphore`) and pipeline barriers can be used to synchronize across queues. Normal semaphores are binary objects that can be waited and signaled on the GPU. Another variant of them, timeline semaphores, are available on some devices as optional feature. Timeline semaphores are basically the same as D3D12 fences, they are counter-based and can also be waited and signaled from the CPU. 
- **GPU / GPU:**
    - **Execution ordering:** Pipeline barriers and events within a queue, pipeline barriers and semaphores across queues. Semaphores can only be waited at the start of a submission and signaled at the end. This is slightly less flexible than separating the concepts of waiting and signaling from command buffers as D3D12 does.  Pipeline barriers and events are discussed in more detail in the next point. Vulkan also has events (`VkEvent`), those objects combine a split barrier and a semaphore that can be set by the CPU and set or waited on by the GPU. In contrast with semaphores, events can also be waited and signaled in the middle of a command buffer, but they cannot be waited by the CPU.
    - **Memory ordering:** Pipeline barriers. These cover the same role as D3D12 resource barriers, but have a few important differences. There are 3 types, memory, buffer and image barriers. Memory barriers are purely dealing with memory ordering, and operate globally on all memory objects.  Buffer barriers operate on a single buffer, they must be used (in addition to semaphores for execution ordering) when synchronizing usage of the same buffer across multiple queues. Image barriers are like buffer barriers but they additionally specify also layout transitions by specifying the previous and next layout. The main difference with D3D12 is that instead of specifying resource states, every barrier must specify stage flags and access. This a huge topic on its own, see [this](https://www.khronos.org/blog/understanding-vulkan-synchronization) and [this](https://themaister.net/blog/2019/08/14/yet-another-blog-explaining-vulkan-synchronization/) blog posts and the [vulkan synchronization examples](https://github.com/khronosgroup/vulkan-docs/wiki/synchronization-examples) for more details. One of the more popular criticisms to Vulkan is that by assuming that any stage can potentially require separate fine grain synchronization the API becomes very complex to use correctly, and a lot of work has to be done by the application even if the hardware has potentially coarser granularity and less strict requirements.  Split barriers are supported through events.
    - **Layout transition:** Image layout transitions must be explicitly specified with pipeline barriers and events. There is a general layout that supports any operation but might be unoptimal, and an unknown layout that does not need to preserve previous image contents.
- **CPU / GPU:**
    - **Execution ordering:** Fences (`VkFence`) are binary objects that can be set on the GPU and waited and reset by the CPU. These are commonly used to wait for commands to complete. Timeline semaphores, discussed above, have strictly more powerful than fences. Events can also be used to have the GPU wait for a signal by the CPU.
    - **Memory ordering:** All CPU mappings are persistent and must be externally synchronized. Explicit flushes and invalidations are only needed if buffers are allocated in memory that does not have the `VK_MEMORY_PROPERTY_HOST_COHERENT_BIT` set. For incoherent memory `vkInvalidateMappedMemoryRanges` must be used after GPU writes and before reading on the CPU, and `vkFlushMappedMemoryRange` after writing on the CPU and before reading on the GPU.

#### CUDA
CUDA is a compute only API, it does not expose graphics functionality and only supports nvidia GPUs. The synchronization model is also very straight forward.

- **Commands:** There are no explicit command buffers, but something very similar to queues is available through streams (`cudaStream_t`). There is also default stream with special semantics that is fully synchronous with respect to other streams and the CPU, we won't consider it in the following points.
    - **Reordering within a queue:** operations in a stream are synchronous, they execute to completion before the following one can start.
    - **Reordering across queues:** operations in different streams are completely asynchronous. Events (`cudaEvent_t`) are boolean synchronization objects that can be signaled and waited on by both the CPU and GPU. They also collect timestamp that can be queried on the CPU to measure elapsed time.
- **GPU / GPU:**
    - **Execution ordering:** events only.
    - **Memory ordering:** no explicit memory ordering operations are needed.  Caches are either coherent or implicitly flushed by the driver between commands.
    - **Layout transition:** no layout transition operations are exposed. Images are available through CUDA textures and I assume they are stored in a tiled layout optimized for 2D local access. Layout transition, if performed, are all handled by the driver.
- **CPU / GPU:**
    - **Execution ordering:** Events and `cudaStreamSynchronize` that wait for a stream to complete.
    - **Memory ordering:** CPU memory that is also visible by the device can be allocated with `cudaMallocHost` or `cudaHostAlloc`. This memory is persistently mapped but does not require explicit cache flushes or invalidation by the application. I assume the driver can in most cases allocate coherent memory for this on modern systems.

## Summary

Here's a table that summarizes synchronization primitives and semantics each API:

|                                | D3D11         | OpenGL                                              | D3D12                          | Vulkan                                                                | CUDA                               |
|--------------------------------|---------------|-----------------------------------------------------|--------------------------------|-----------------------------------------------------------------------|------------------------------------|
| **Command queues**             | no            | no                                                  | yes                            | yes                                                                   | yes                                |
| **Reordering within queues**   | n/a           | n/a                                                 | yes                            | yes                                                                   | no                                 |
| **Reordering across queues**   | n/a           | n/a                                                 | yes                            | yes                                                                   | yes                                |
| **GPU/GPU execution ordering** | implicit      | implicit                                            | Resource barriers, ID3D12Fence | Pipeline barriers, VkEvent, VkSemaphore, timeline semaphores          | cudaEvent_t                        |
| **GPU/GPU memory ordering**    | implicit      | glMemoryBarrier (only compute)                      | Resource barriers              | Pipeline barriers                                                     | implicit                           |
| **Layout transitions**         | implicit      | implicit                                            | Transition barriers            | Image barriers                                                        | implicit                           |
| **CPU/GPU execution ordering** | implicit      | GLsync                                              | ID3D12Fence                    | VkFence, VkEvent, timeline semaphores                                 | cudaEvent_t, cudaStreamSynchronize |
| **CPU/GPU memory ordering**    | map and unmap | map and unmap, glFlushMappedBufferRange or coherent | map and unmap, coherent        | vkInvalidateMappedMemoryRanges and vkFlushMappedMemoryRange, coherent | implicit or coherent               |

<!-- ## Bonus: API interoperability, external semaphores and compositors -->
