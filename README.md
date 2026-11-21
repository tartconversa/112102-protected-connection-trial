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

## 试点分析应用

`pilot` 包把试验分析与可兑现决策做成可反复使用的命令行应用，仅需标准库。
状态（台账与运行档案）写入 `var/`（不入库），来源资料保持只读。

```sh
# 分析人员：按原始邀约时间复核比较（ITT，含未购票/未飞成/改走地面）
python -m pilot analyze [--observation-end N] [--alpha 0.05]

# 运营人员：查看各枢纽后续窗口的可出售范围与阻塞原因
python -m pilot windows [--day N]

# 运营人员：提交下一期窗口售票方案（原子校验，已被接受方案占用
# 的空余量不会重复生效）
python -m pilot submit --hub west --window W7 --seats 2 --start 50 --end 56

# 收到履约更正或地面承运人缩减承诺时，核对受影响窗口与既有出票
python -m pilot correct contract --contract-id C4 --pool P7 --hub east \
    --window W7 --seats 1 --available-day 44
python -m pilot correct carrier --event-id CE5 --order O1 --pool P7 \
    --hub west --seats 3 --state-value carrier_accepted --event-day 45

# 运营侧暂停 / 审计回查
python -m pilot pause --hub west --window W7 --from-day 50 --reason "检修"
python -m pilot audit [--day N]
```

分析口径（随每次运行存档于 `var/runs/<id>/`）：按发出邀约时的分组（ITT）；
聚类单位 = 席位池（同池旅客不独立）；改签事件去重；跨出观察期未决的行程
不计成败；未购票在主分析中计为未完成、敏感性分析中剔除；不确定性以
池级聚类稳健区间与符合设计的精确随机化检验共同呈现。容量口径：合同
seats 为池级保证、跨枢纽不可相加；已出票保护保留至承运接手或旅客解除；
可售量须在每个并发延误情景下仍可兑现；confirmed_first 优先保障；
试验显示改善但只能靠挤占已承诺席位时不列入可扩围名单。
