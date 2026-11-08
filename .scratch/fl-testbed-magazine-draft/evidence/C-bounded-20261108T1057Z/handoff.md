C12首次smoke在prepare失败：fl04 iw mismatch，未acquire_radio，未启动UFTP/HTTP。不重跑，不做18/22/26或model。原始transfer=false、cleanup=true、restore=false不覆盖。

独立11板audit-fl*.json全部rc0、ok=true：实际149/HT40/LGI，server3/client4，GET_FEC8/12，server18/2，15W mode0，f376a1f新release manifest安装全文件SHA与冻结source Python SHA通过；实验unit inactive、无proto186路由，server IDLE、十端READY/IDLE。没有修改FEC14。独立恢复另列为true（实际终态通过；未执行额外RF恢复事务）。

partials：未进入传输，samples=[]，actualUFTPrate=null，不称零吞吐。fl04失败当时iw原文未被runner输出，不推断其瞬间信道/MCS。短窗口journal有缺有效心跳后寻频自愈记录；后续实际审计已149。无工具修补、无配置/service/timeout/registry修改、无部署或新抓包。
