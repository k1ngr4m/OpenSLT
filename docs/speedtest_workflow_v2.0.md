测试流程（以rem_mm软核做市为例，不同版本的测试可能有细微的不同）
1. 选择需要执行的方案后点击进行测速
    测速流程
    - 1. 获取测试服务器的基本软硬件配置情况（举例，具体用命令行去获取结果）
        a. rem系统服务器：
            1. ip地址
            2. 网卡型号：Exablaze ExaNIC X25 *2
            3. 机器型号：ATZ-308/ACE Z690 UNIFY(MS-7D28)
            4. 操作系统版本：Red Hat Enterprise Linux Server release 7.9(Maipo)
            5. CPU型号：13th Gen Intel(R) Core(TM) i9-13900KS 5.8主频
        b. 模拟市场服务器：
            1. ip地址
            2. 操作系统版本：CentOS Linux release 7.6.1810
            3. CPU型号：Intel(R) Core(TM) i9-7980XE CPU@2.60GHz 16核
        c. 发单工具：
            1. ip地址
            2. 操作系统版本：Red Hat Enterprise Linux Server release 7.9(Maipo)
            3. CPU型号：Intel(R) Xeon(R) Gold 6244 CPU@3.60GHz 16核
        获取后将得到的结果展示并保存(怎么存到数据库中？表的设计你自己思考一下)
    - 2. 获取数据库配置信息
        一般所需要用到的配置项在*_config的数据库的t_global_settings的表中
        a. CLIENT_REQ_BIND_CPU
        b. MARKET_RESP_BIND_CPU
        c. RINGBUFFER_RSP_BIND_CPU
        d. TCP_SERVER_BIND_CPU
        e. CLIENT_REQ_ENABLE
        f. CLIENT_REQ_USING_DEV
        g. MARKET_RESP_ENABLE
        h. MARKET_RESQ_DEV
        i. REM_TO_MKT_MESSAGE_DROPCOPY_ENABLE
        j. CLIENT_TO_REM_MESSAGE_DROPCOPY_ENABLE
        k. MARKET_SESSION_IDLE_REPROT_LOG
        l. ACCOUNT_QUANTITY
        m. WARM_ORDER_REPORT_USEC
        n. ENABLE_PERF_COUNTER
        o. ENABLE_RINGBUFFER_RSP
        p. ENABLE_RINGBUFFER_REQ
        q. ASYNC_MKT_MSG_PROC
        r. USER_TOKEN_CANCEL_ENABLE
        s. CLIENT_OT_CONNECT_MODE
        t. EXANIC_IP_FILTER_FLAG
        u. ENABLE_REPORT_TIMESTAMP
        v. X25_KEY_VALUE
        获取后将得到的结果展示并保存(怎么存到数据库中？表的设计你自己思考一下)