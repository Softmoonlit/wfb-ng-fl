# 15W只读判据更正

原supplemental-actual-power-audit.json的false和错误expected保留，不作覆盖。错误在于将CPU能力上限cpuinfo_max_freq和BPMP EMC debug max_rate强制等同于nvpmodel功耗策略限制。

Linux CPUFreq官方文档将cpuinfo_max_freq定义为“Maximum possible operating frequency”，scaling_max_freq定义为“Maximum frequency ... allowed to be running at”。本地官方nvpmodel profile /etc/nvpmodel/nvpmodel_p3767_0003_super.conf中，CPU MAX_FREQ/KNEXT实际写scaling_max_freq；GPU MAX_FREQ_KNEXT写/sys/devices/platform/17000000.gpu/devfreq_dev/max_freq；EMC MAX_FREQ_KNEXT写/sys/kernel/nvpmodel_clk_cap/emc（旧路径为/sys/kernel/nvpmodel_emc_cap/emc_iso_cap）。15W profile值为1497600/612000000/2133000000。

十板修正只读复核：CPU online0-5、scaling_max1497600、GPUmax612000000、EMCcap2133000000、mrq锁0、nvpmodel.service success/0和jtop inactive全部通过；完整实际radio/FEC/native/source恢复也通过。没有改电源、重启或改15W。能力字段1728000/3199000000仅记录，不据此判定功耗策略失败。

20/5模型600秒超时和原始false/true/false不变，不能因判据更正改作试验成功。30/5在独立continuation中按剩余两次授权推进，不重跑20。
