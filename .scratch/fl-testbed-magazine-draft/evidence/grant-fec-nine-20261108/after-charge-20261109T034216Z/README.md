# 充电重启后新批次

本批十板初始审计和九端READY/IDLE通过，无待关机任务/旧活动unit。冻结runner三文件bundle保持ab8697不变，未联系04、未部署native。20/5 small一次通过，DL12.1935s/UL15.5041s，原始三个flag与独立恢复均true。20/5模型一次下行65.9111s，上行600.0286s到期，8/9完整，05最后可见18808832字节；原始transfer=false/cleanup=true/restore=false保留，独立恢复审计后来通过单列。

补充电源只读审计保存了实际policy与capability字段。十板CPU scaling_max1497600、GPU max612000000、nvpmodel EMCcap2133000000、mrq锁0正确，nvpmodel.service success/0，jtop inactive；cpuinfo硬件上限1728000、debug EMC max_rate3199000000不同，不能简单视作有效策略失败。补充严格比较时设置边界STOP，彼时model已运行，未强杀；本档收尾后driver真实暂停并退出。30/5 small/model均未启动，server保持20/5且实际无线已恢复149/HT40。待父核定字段口径后另行控制，不自动重跑。
