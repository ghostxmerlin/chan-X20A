# 遥控器开机与连接设置回归

从仓库根目录运行：

```sh
bash upstream/StackChan/remote/tests/run-host-tests.sh
env PATH="$PWD/.tools/py313bin:$PATH" bash scripts/build-remote.sh
```

开机读取自己的 NVS namespace `remote_cfg` / blob `connection`，缺省为 CH6 / Receiver ID1，直接进入 JOYSTICK。连接设置的启动读取与无变更确认均不写 NVS；这不代表 Wi-Fi/PHY 底层不会更新自身校准。A 键循环为 JOYSTICK → IMU → SETUP → JOYSTICK；B 键在设置页选择 Channel / Receiver ID，摇杆上下调整，A 键确认保存并开始控制。只有确认有效且变化的两个值才一起写入单个版本化 blob 并 commit，然后读回核对。候选值越界时保留最后有效运行值；损坏、未知版本或读取失败时启动使用 6/1 且不改写原存储。广播 ID0 仍须用户显式选择。

ESP-IDF 5.5.4 的 `nvs_set_blob` 本身会写 Flash，`commit` 不是可回滚事务。任何写/commit 错误都会保留为非成功结果并输出未确认警告；若读回有效，运行值采用实际读回的完整 pair；若读回失败，运行值保留上一有效 pair。`ESP_ERR_NVS_REMOVE_FAILED` 可能即时读旧、重启后得到新值，因此错误时不承诺旧持久值不变，不自动擦除、重新初始化或回滚写入。

主机测试执行生产 NVS 配置逻辑、实际 A/B 键处理和 UI 初始化/设置页代码，NVS、按键和 LVGL 外设边界使用测试替身。覆盖默认只读、重启读取、单次确认写、未改变不写、边界值、无效格式/版本/范围、set 已生效却报错、commit 故障、REMOVE_FAILED 即时旧/重启新、读回失败、按钮顺序、当前下拉选项和退出后迟到设置更新。它不验证真实闪存掉电恢复、屏幕渲染、无线连接或硬件按键。

固定 ESP-IDF 5.5.4 构建由既有 `scripts/build-remote.sh` 隔离执行，不修改原依赖锁、manifest、sdkconfig，不执行刷机；结果见 `logs/remote-center-v1/result.json`。此前识别的遥控器为 ESP32-PICO-D4 / 4 MB / factory 0x10000，后续仍须核对实际 USB 目标、芯片与分区，只写经过核对的应用区。源码上游 sdkconfig 的 2 MB 不能作为实物容量或通用刷机依据。

实机待验：断电重开直接进入 JOYSTICK，确认 CH6/ID1 接收；循环进入 IMU/SETUP；改有效值并确认后重开仍保留，再恢复 CH6/ID1；确认中心键、心跳与接收端的四向模式切换正常。未在本次主机回归中操作硬件。
