# Figure 4 第一版生成规格

## 来源与用户确认

来源正文：/home/kilome/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/prototypes/IEEE-Communications-Magazine中文初稿.md
规范：/home/kilome/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/spec.md，第 13 行后续局部修订。
用户要求本版直接采用正文数值作为结果数据，不加“示意数据”“待核实”水印；后续真实实验完成后修改 SVG。当前先交付 PNG，确认效果后再制作可编辑 SVG。图内全部英文。不得修改正文或 spec。

## 图的任务

为 IEEE Communications Magazine 生成 Figure 4 综合实验结果组合图。只展示三种预设无线配置的下行有效吞吐、客户端更新提交情况、完整轮次耗时，以及全部训练尝试的完成情况。删除硬件和空间部署，不绘制节点、设备、场景、室内地图或照片。

## 精确数据

配置顺序及固定配色：
- Robustness-oriented：蓝色 #3977A8；20 MHz, MCS 2, long GI, RS(8,14)。
- Balanced：青绿色 #268A78；20 MHz, MCS 4, short GI, RS(8,12)。
- Throughput-oriented：橙色 #D88936；40 MHz, MCS 6, short GI, RS(8,10)。

(a) Downlink goodput
- Robustness-oriented: median 11.2 Mbps; IQR 0.6 Mbps; delivery time 30.0 s.
- Balanced: median 22.4 Mbps; IQR 2.2 Mbps; delivery time 15.0 s.
- Throughput-oriented: median 68.5 Mbps; IQR 6.8 Mbps; delivery time 4.9 s.
- Goodput is model size divided by total time until all target clients have received and validated the model. Model size is 40 MiB. Do not multiply by client count.

(b) Client update submission
- Robustness-oriented: client submission-time IQR 1.8 s. Median slowest-client time is not reported. No numeric position can be assigned to its median.
- Balanced: median slowest-client submission time 39.6 s.
- Throughput-oriented: median slowest-client submission time 76.2 s; maximum observed 104.5 s.
- No individual client submission times, minimum values, Q1, Q3, or per-run observations are supplied. Do not invent them.

(c) Round duration
- Robustness-oriented: median 96.5 s; IQR 3.5 s.
- Balanced: median 52.8 s; IQR 4.8 s.
- Throughput-oriented: median and IQR not reported. Show 'Not reported' without a quantitative bar.
- Duration is from model publication through complete update collection and aggregation. Do not stack downlink, training and uplink as additive phases because they overlap.

(d) Training outcomes
- Each configuration: 10 independent training attempts, 5 rounds per training attempt. The attempt, not an individual round, is the independent repeat.
- Robustness-oriented: 10 completed, 0 timed out.
- Balanced: 10 completed, 0 timed out.
- Throughput-oriented: 8 completed, 2 timed out.
- Do not label these as 10 clients or 10 rounds. Do not imply ordering of successful and failed attempts.

## 构图与绘图要求

Create a polished, publication-quality statistical results figure with a white background, a landscape canvas around 3000 x 2200 pixels (approximately 4:3), a clean 2-by-2 panel layout, generous whitespace, compact sans-serif typography, and thin dark axes. English only. No overall banner or large Figure 4 title inside the image. Panel letters (a)-(d) aligned at each top-left. All text should remain readable at a journal's two-column width.

This is a quantitative results figure. Strictly exclude cute characters, mascots, hardware icons, decorative illustrations, photographs, gradients, 3D bars, rounded dashboard cards, shadows, and flowchart arrows. These specific plotting requirements override any generic stylistic suggestions in the surrounding image-generation request.

Use aligned horizontal bar plots for panels (a) and (c), and horizontal dot plots for the two available medians in (b). Category labels must consistently read 'Robustness-oriented', 'Balanced', and 'Throughput-oriented', with a line break if needed. Keep the configuration color consistent across panels. All quantitative axes begin at zero. Use light, sparse gridlines.

Panel (a): title 'Downlink goodput'; axis 'Goodput (Mbps)', range 0-80. Bar lengths exactly 11.2, 22.4, 68.5. Bold direct value labels. Adjacent text columns 'IQR (Mbps)' with 0.6, 2.2, 6.8 and 'Delivery (s)' with 30.0, 15.0, 4.9, using a clear tabular alignment. The IQR is only supplied as a width: do not fabricate endpoints or symmetric error bars.

Panel (b): title 'Client update submission'; axis 'Slowest-client submission time (s)', range 0-120. For Balanced place a filled dot at 39.6 and label '39.6'. For Throughput-oriented place a filled dot at 76.2 and a separate hollow diamond at 104.5 labeled 'Max 104.5'. Do not draw a distribution or full range. For Robustness-oriented show text 'Median not reported' in its row, and a second note 'Client submission-time IQR: 1.8 s'. That 1.8 s is a different statistic and must not be plotted as a median on the common axis. Add a small key 'Dot: median | Diamond: maximum' if necessary.

Panel (c): title 'Round duration'; axis 'Round duration (s)', range 0-120. Bars at 96.5 and 52.8 for the first two configurations. Direct labels '96.5' and '52.8', with text 'IQR 3.5 s' and 'IQR 4.8 s'. Throughput-oriented row has the text 'Not reported' and no bar. No inferred third duration and no zero-duration claim. Do not draw error bars or box plots.

Panel (d): title 'Training outcomes'; axis 'Independent training attempts', range 0-10. Each row has a horizontal stacked count bar: completed section uses the configuration color; timeout section has neutral gray diagonal hatching. The first two rows show completed length 10 and timeout length 0; the third shows completed length 8 and timeout length 2. Direct labels '10 completed / 0 timed out', '10 completed / 0 timed out', and '8 completed / 2 timed out'. Include a concise legend 'Completed' and 'Timed out'. Counts do not indicate attempt order.

One unobtrusive footer: '10 independent training attempts per configuration; 5 rounds per attempt. IQR: interquartile range.' No 'synthetic', 'illustrative', 'preliminary', or 'pending verification' watermark. Treat the supplied manuscript values as the figure inputs, while never creating absent values, synthetic samples, box plots, violin plots, or per-client timelines.
