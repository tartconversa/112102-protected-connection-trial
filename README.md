# 接驳保护试验与可兑现的换乘承诺

城际接驳试营、旅客行程及承运协议的合成记录。 所有标识、金额、地名和观察值均为合成数据。

## 来源资料

- `sources/randomization.csv`
- `sources/invitations.csv`
- `sources/journey_events.csv`
- `sources/ordinary_passenger.csv`
- `sources/rescue_contracts.csv`
- `sources/disruption_scenarios.csv`
- `sources/carrier_events.csv`

时间以试验相对日表示。每周的东、西枢纽构成一个席位池，试验先在周级分配池内保护比例，再在中间比例的周分配枢纽。邀约记录包含所有入组家庭，party 为同行人数，booked 表示接受原预约。地面行程记录表示最终目的地接驳，replaces 指被替代的接驳事件。

ordinary_passenger 只含未参与邀约的旅客。观测标为 no 时，人数来自候车登记，missed 暂无核验值。

rescue_contracts 的 seats 是池级保证，多个枢纽引用同一池不可相加。同一 simultaneous_group 为可能共同出现的延误，seats 为所需承接人数。承运记录的 offered、confirmed、carrier_accepted、released 依次表示发出保护、权益已生效、承运侧接手或撤销；保留各次独立事实。

## 本地检查

使用 Python 3.14，仅需标准库。在项目目录执行以下命令检查输入表结构、事件引用和基础记录覆盖。

```sh
python -m unittest discover -s tests -v
```
