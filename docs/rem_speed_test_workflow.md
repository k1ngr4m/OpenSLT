Rem期货测速流程
1测速环境准备（包括模拟市场、发单工具）+
软核做市-51.107，整合版二期-51.8(NF11)，MG11做市-51.129(MG11)+
①检查服务器环境（绑核隔离，性能模式，内存/磁盘/负载，有无其他进程占用等）+
②升级rem版本+
③检查柜台相关配置（start_rem_all.sh启动方式，软核slexasock指定交易所；数据库
全局配置表-主要检查cpu绑核，席位配置绑核等）
④模拟市场检查xm1（traderesptype改为"0全部接受）
目前测速使用的模拟市场机器有51.138/51.129/51.8（非强制，主要是选择日常负载较
小的机器，避升51.21/51.22这些较多人使用的模拟市场机器）
51.8@root:/home/user0/px/czcemkt6v2.2&dcev7mkt&gfexmkt+
51.129@root:/home/user0/px/mmcffexmkt&shfemkt 12&dce v7mkt+
51.138@root:/home/user0/px/shfe mkt 12
-上面路径下的模拟市场程序不一定为最新版本，实际使用时如遇报错可到sVvm或标准
模拟市场环境复制最新版本。+
③发单工具
使用密集发单工具eesefvitraderbinaryapi test、eeszftraderbinaryapitest
http:/10.1.52.200/svn/qatest/remtools/二期快速下单工具+
http:/10.1.52.200/svn/qatestremtools/做市版急速下单工具+
（特殊场景可使用交易api编写支持个性化下撤单）+
密集发单工具，部分参数介绍：
"entertimes"每批发单数量，12个shfe席位*100流控+
"enterbatch”批次，默认10批，12000单
"batchintervaltime"批次间隔，单位us，黑认1s+
"symbol"合约
"idx"索引（软核精简协议时使用）
"price"价格+
"batchordernum”复制单批次，黑认0
"enterintervaltime”发单间隔，单位纳秒ns一一黑认1000ns+
eeszf trader binary_apitest ees_zf_trader_api_test_conf.xml启动→help→
neworder
exportZF_ATTR=interface=p5p1（zf配置发单服务器网卡口，根据实际更改）
发单工具可用机器有51.31/51.129/51.181等（非强制，主要选择日常负载较小机器，及
是否支持180网段）
51.31@root:/home/user0/px/sendorderfmdce
51.31root:/home/user0/px/two_send.tools
下撤单命令：+
new_order:普通单下单
new_ordersimple:普通单下单（精简协议）
newquote:报价单下单
newquote_simple：报价单下单（精简协议）
newarbiorder：套利单下单
new_arbiordersimple：套利单下单（精简协议）
cxlorder:普通单撤单（先下单再撤单）
cxlquote:报价单撤单（先下单再撤单）
2抓包准备（接线图、抓包工具）+
当rem柜台环境完毕，且能顺利发单，进入下一步。+
1绘制接线图，机房帮助接线+
几个环境接线图如下（可查看本文档同路径下rem期货测速接线图.sdr）
②抓包工具路径：
240528/tcpdump
使用start_slnic_dump.sh、stop_slnic_dump.sh启停，会生成四个方向的slnic*·pcapng文
件，用pcapmerge.tool工具合并成单个mergepcap.pcap（命令/pcapmergetool slnic*）
--补充：
/home/user0/slnic/路径下，SLNIC_NF11_10g_10g版本支持10G+10G，SLNIC_NF11_1g_10g
版本支持1G+10G（0/1口为1G，2/3口为10G）。两个版本切换，需重新固化+断电重启。（详
参slnicnf11使用手册.docx）
③机房接线完成后，当即启动抓包工具（start_slnic_dump.sh），下单，检查四方向
slnic*·pcapng是否抓取到对应包。若有问题，让机房检查接线。
L/
3抓包数据解析+
当抓包无误，进入下一步。
软核做市：
51.210@root:/home/user0/px/px/softcffexspeedanalvsis
51.210@root:/home/user0/px/px/softcffexspeedanalysisv2
51.210@root:/home/user0/px/px/softshfespeedanalysisv2
51.210@root:/home/user0/px/px/softczcespeedanalvsis
51.210@root:/home/user0/px/px/softdcespeedanalysisv7
51.210@root:/home/user0/px/px/softgfexspeedanalvsis+
整合版二期：
51.210@root:/home/user0/px/px/hwcffex14142.0
51.210@root:/home/user0/px/px/hwshfe14142.0+
MG11 :
51.210@root:/home/user0/px/px/mg11+
以softcffexspeedanalysisv2为例，简个配置+
1 config.xml 默认账户100001
2 instance.xml 配置席位号与连接实例号
3 soft_cffex_speed_analysis.xml 
    quoto_file_name  指定抓包文件名
    rem_client_ip 指定发单客户端ip
    market_ip 指定市场ip
4 rem数据库导出交易编码表t_account_exchange_code.csv，至解析工具路径下+
5 启动顺序：抓包工具→模拟市场/rem柜台一发单+
必须抓取柜台连接席位时的密钥，用于解析。+
6 发单结束，导出订单表t_fut_orders.csv/t_fut_quotes.csv/t_fut_arbi_orders.csv，至解析
工具路径下
繁复手动导出耗时，可借助脚本：+
7 抓包结束，将mergepcap.pcap，至解析工具路径下（需转变成.pcapng）
8 执行解析/soft_cffex_speed_analysis_v2 soft_cffex_speed_analysis.xml
write.clt_new.to_mkt→普通单/套利单，下单上行+
write_cltaction_tomkt→普通单/套利单，撤单上行
write_cltguoteaction_tomkt→报价单，撤单上行
write_mktaccept.to_clt→普通单/套利单，下单下行+
write_cltnewquotetomkt→套利单，下单上行+
write_mktguoteaccept.to_clt→套利单，下单下行+
9 若解析工具执行报错，检查配置文件格式；若解析结果为空，检查配置文件内容（
方向、pcapng包等）
+
4上下行数据统计分析+
当成功解析出所需上下行csv文件（非空），进入下一步。+
1使用pv脚本，分析统计对应的上下行数据+
/statistics.py rem_client_new_to_market_speed_20260617.csv
avg平均值，max最大值，min最小值，md中位数，std标准差，cnt总条数，mostfrequent
占比最多值+
②简单验证，若结果偏差过大，需排查检查相关配置（柜台版本、绑核隔离、软硬件加
速等），重新调试。+
初步调试通过后，再继续后续的多场景测速。+
←
5整体流程+
当初步调试通过，开始进行多场景测速。
1根据任务描述，整理所需测速场景。+
②准备测速结果登记文档（对内，登记结果应尽可能详细）
文件→测速解析一结果登记文档（视情况保留测速文件）
→停rem/清订单表→重启抓包→重启rem→重复流程+
④整理测速结果，输出测速结论。
5性能测速报告。+