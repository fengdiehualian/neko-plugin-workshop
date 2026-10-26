"""Data-driven Scratch block registry for the MO strategy editor.

Only Chinese labels are exposed to the editor. Internal identifiers and
executors stay in this module so a saved strategy never needs raw bridge
commands or game object identifiers in user-authored text.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _input(name: str, kind: str, *, default: Any = None, source: str = "", options: tuple[str, ...] = ()) -> dict[str, Any]:
    value: dict[str, Any] = {"name_zh": name, "type": kind}
    if default is not None:
        value["default"] = default
    if source:
        value["menu_source"] = source
    if options:
        value["options"] = list(options)
    return value


def _block(
    identifier: str, name: str, shape: str, *, output: str = "",
    inputs: tuple[dict[str, Any], ...] = (), stacks: tuple[str, ...] = (),
    executor: str = "", editor: str = "",
) -> dict[str, Any]:
    value: dict[str, Any] = {"id": identifier, "name_zh": name, "shape": shape}
    if output:
        value["output"] = output
    if inputs:
        value["inputs"] = list(inputs)
    if stacks:
        value["stacks"] = list(stacks)
    if executor:
        value["executor"] = executor
    if editor:
        value["editor"] = editor
    return value


RELATIONS = ("我方", "敌方", "中立", "任意关系")
FACTIONS = (
    "任意子阵营", "美国", "欧洲联盟", "太平洋阵线", "苏俄", "拉丁同盟", "中国",
    "心灵军团", "厄普西隆总部", "天蝎组织", "焚风子阵营一", "焚风子阵营二", "焚风子阵营三",
)
UNIT_GROUPS = ("任意类别", "步兵", "装甲", "飞机", "海军", "采矿单位", "建设单位", "运输单位", "支援单位")
BUILDING_GROUPS = ("任意建筑", "基地车", "电厂", "兵营", "矿场", "重工", "机场", "科技建筑", "防御建筑", "超级武器", "中立科技建筑", "建筑插件")


BLOCK_GROUPS: tuple[dict[str, Any], ...] = (
    {"name_zh": "事件", "color": "#ffbf00", "blocks": [
        _block("事件.对局开始", "当对局开始", "帽子", output="事件"),
        _block("事件.自主授权", "当玩家授权自主作战", "帽子", output="事件"),
        _block("事件.敌军出现", "当敌军进入视野", "帽子", output="事件"),
        _block("事件.基地受袭", "当基地受到攻击", "帽子", output="事件"),
        _block("事件.矿车受袭", "当采矿车受到攻击", "帽子", output="事件"),
        _block("事件.单位受袭", "当指定单位受到攻击", "帽子", output="事件", inputs=(_input("单位", "单位集合"),)),
        _block("事件.资金不足", "当资金低于", "帽子", output="事件", inputs=(_input("资金", "数字", default=5000),)),
        _block("事件.科技升级", "当科技等级改变", "帽子", output="事件"),
        _block("事件.阶段改变", "当任务阶段改变", "帽子", output="事件"),
        _block("事件.战场变化", "当战场信息更新", "帽子", output="事件"),
        _block("事件.对局结束", "当对局结束", "帽子", output="事件"),
    ]},
    {"name_zh": "对局", "color": "#f2c94c", "blocks": [
        _block("对局.已经开始", "对局已经开始吗", "布尔", output="布尔"),
        _block("对局.模式", "当前对局模式", "报告器", output="文字", inputs=(_input("模式", "菜单", options=("遭遇战", "战役", "多人战役合作", "多人PVP", "混战", "人机混合")),)),
        _block("对局.玩家数量", "当前玩家数量", "报告器", output="数字"),
        _block("对局.队伍关系", "读取队伍关系", "报告器", output="文字", inputs=(_input("玩家一", "文字输入"), _input("玩家二", "文字输入"))),
        _block("对局.难度", "当前人机难度", "报告器", output="文字"),
        _block("对局.已取消", "对局已经取消吗", "布尔", output="布尔"),
        _block("对局.读取状态", "重新读取对局状态", "命令"),
    ]},
    {"name_zh": "战役", "color": "#e5a93d", "blocks": [
        _block("战役.任务编号", "当前任务编号", "报告器", output="文字"),
        _block("战役.阶段", "当前任务阶段", "报告器", output="文字"),
        _block("战役.阶段目标", "当前阶段目标文本", "报告器", output="文字"),
        _block("战役.有援军", "当前阶段有援军吗", "布尔", output="布尔"),
        _block("战役.特殊限制", "当前任务有特殊限制吗", "布尔", output="布尔"),
        _block("战役.胜利条件", "任务胜利条件已满足吗", "布尔", output="布尔"),
        _block("战役.完成阶段", "报告阶段目标完成", "命令"),
    ]},
    {"name_zh": "控制", "color": "#ffab19", "blocks": [
        _block("控制.如果", "如果", "容器", inputs=(_input("条件", "布尔"),), stacks=("那么",)),
        _block("控制.如果否则", "如果否则", "容器", inputs=(_input("条件", "布尔"),), stacks=("那么", "否则")),
        _block("控制.重复次数", "重复执行", "容器", inputs=(_input("次数", "数字", default=10),), stacks=("执行",)),
        _block("控制.重复直到", "重复直到", "容器", inputs=(_input("条件", "布尔"),), stacks=("执行",)),
        _block("控制.持续监视", "持续监视", "容器", inputs=(_input("对象", "对象引用"),), stacks=("执行",)),
        _block("控制.并行", "并行执行", "容器", stacks=("分支一", "分支二")),
        _block("控制.等待条件", "等待直到", "命令", inputs=(_input("条件", "布尔"),)),
        _block("控制.等待秒数", "等待秒数", "命令", inputs=(_input("秒数", "数字", default=3),)),
        _block("控制.超时回退", "超时后回退", "容器", inputs=(_input("秒数", "数字", default=15),), stacks=("正常执行", "超时执行")),
        _block("控制.调用策略", "调用策略", "命令", inputs=(_input("策略", "菜单", source="策略"),)),
        _block("控制.停止策略", "停止这条策略", "命令"),
        _block("控制.重新判断", "重新读取战场并判断", "命令"),
    ]},
    {"name_zh": "侦测", "color": "#5cb1d6", "blocks": [
        _block("侦测.对局开始", "对局已经开始吗", "布尔", output="布尔"),
        _block("侦测.桥接正常", "游戏连接正常吗", "布尔", output="布尔"),
        _block("侦测.存在敌人", "视野内有敌人吗", "布尔", output="布尔"),
        _block("侦测.对象受伤", "对象受伤了吗", "布尔", output="布尔", inputs=(_input("对象", "对象引用"),)),
        _block("侦测.建筑缺电", "建筑缺电了吗", "布尔", output="布尔", inputs=(_input("建筑", "建筑集合"),)),
        _block("侦测.目标可见", "目标仍然可见吗", "布尔", output="布尔", inputs=(_input("目标", "目标引用"),)),
        _block("侦测.矿车工作", "采矿车正在工作吗", "布尔", output="布尔", inputs=(_input("采矿车", "单位集合"),)),
        _block("侦测.生产空闲", "指定产线空闲吗", "布尔", output="布尔", inputs=(_input("产线", "菜单", options=("建筑", "支援建筑", "步兵", "装甲", "飞机", "海军")),)),
        _block("侦测.单位数量", "单位数量", "报告器", output="数字", inputs=(_input("单位", "单位集合"),)),
        _block("侦测.我方资金", "我方现有资金", "报告器", output="数字"),
        _block("侦测.资金变化", "每分钟资金变化", "报告器", output="数字"),
        _block("侦测.矿区储量", "矿区剩余比例", "报告器", output="数字", inputs=(_input("矿区", "位置引用"),)),
        _block("侦测.生命比例", "对象生命百分比", "报告器", output="数字", inputs=(_input("对象", "对象引用"),)),
        _block("侦测.双方战力比", "我方与敌方战斗力之比", "报告器", output="数字"),
        _block("侦测.敌方阵营", "识别到的敌方阵营", "报告器", output="文字"),
        _block("侦测.主要威胁", "当前主要威胁", "报告器", output="文字"),
        _block("侦测.战局阶段", "当前战局阶段", "报告器", output="文字"),
        _block("侦测.经济状态", "当前经济状态", "报告器", output="文字"),
        _block("侦测.兵力态势", "当前兵力态势", "报告器", output="文字"),
        _block("侦测.地形类型", "当前交战地形", "报告器", output="文字"),
    ]},
    {"name_zh": "运算", "color": "#59c059", "blocks": [
        _block("运算.大于", "大于", "布尔", output="布尔", inputs=(_input("左值", "数字"), _input("右值", "数字"))),
        _block("运算.小于", "小于", "布尔", output="布尔", inputs=(_input("左值", "数字"), _input("右值", "数字"))),
        _block("运算.大于等于", "大于或等于", "布尔", output="布尔", inputs=(_input("左值", "数字"), _input("右值", "数字"))),
        _block("运算.小于等于", "小于或等于", "布尔", output="布尔", inputs=(_input("左值", "数字"), _input("右值", "数字"))),
        _block("运算.等于", "等于", "布尔", output="布尔", inputs=(_input("左值", "任意值"), _input("右值", "任意值"))),
        _block("运算.并且", "并且", "布尔", output="布尔", inputs=(_input("条件一", "布尔"), _input("条件二", "布尔"))),
        _block("运算.或者", "或者", "布尔", output="布尔", inputs=(_input("条件一", "布尔"), _input("条件二", "布尔"))),
        _block("运算.取反", "不成立", "布尔", output="布尔", inputs=(_input("条件", "布尔"),)),
        _block("运算.相加", "相加", "报告器", output="数字", inputs=(_input("数字一", "数字"), _input("数字二", "数字"))),
        _block("运算.相减", "相减", "报告器", output="数字", inputs=(_input("数字一", "数字"), _input("数字二", "数字"))),
        _block("运算.相乘", "相乘", "报告器", output="数字", inputs=(_input("数字一", "数字"), _input("数字二", "数字"))),
        _block("运算.相除", "相除", "报告器", output="数字", inputs=(_input("数字一", "数字"), _input("数字二", "数字"))),
        _block("运算.随机数", "随机整数", "报告器", output="数字", inputs=(_input("最小值", "数字", default=1), _input("最大值", "数字", default=10))),
        _block("运算.数字", "数字", "报告器", output="数字", inputs=(_input("数值", "数字输入", default=10),)),
        _block("运算.文字", "文字", "报告器", output="文字", inputs=(_input("内容", "文字输入", default=""),)),
    ]},
    {"name_zh": "数量", "color": "#f5a623", "blocks": [
        _block("数量.数量", "数量", "报告器", output="数字", inputs=(_input("对象", "对象引用"),)),
        _block("数量.比例", "比例", "报告器", output="数字", inputs=(_input("部分", "数字"), _input("整体", "数字"))),
        _block("数量.局部兵力", "局部兵力数量", "报告器", output="数字", inputs=(_input("区域", "位置引用"),)),
        _block("数量.战斗力", "战斗力估值", "报告器", output="数字", inputs=(_input("对象", "对象引用"),)),
        _block("数量.变化速度", "数量变化速度", "报告器", output="数字", inputs=(_input("对象", "对象引用"),)),
        _block("数量.达到", "数量达到目标了吗", "布尔", output="布尔", inputs=(_input("对象", "对象引用"), _input("目标数量", "数字"))),
    ]},
    {"name_zh": "流程", "color": "#ffab19", "blocks": [
        _block("流程.顺序", "按顺序执行", "容器", stacks=("步骤",)),
        _block("流程.并行", "同时执行多条产线", "容器", stacks=("建筑产线", "部队产线", "支援产线")),
        _block("流程.分支", "根据条件选择分支", "容器", inputs=(_input("条件", "布尔"),), stacks=("满足时", "不满足时")),
        _block("流程.循环", "循环检查并执行", "容器", inputs=(_input("条件", "布尔"),), stacks=("循环内容",)),
        _block("流程.等待回执", "等待游戏回执", "命令", inputs=(_input("验证", "布尔"),)),
        _block("流程.超时重试", "超时后重试", "容器", inputs=(_input("秒数", "数字", default=10), _input("次数", "数字", default=3)), stacks=("尝试", "失败后")),
    ]},
    {"name_zh": "单位", "color": "#4c97ff", "blocks": [
        _block("单位.选择", "选择单位", "报告器", output="单位集合", editor="级联单位选择器", inputs=(
            _input("关系", "菜单", default="我方", options=RELATIONS),
            _input("子阵营", "菜单", default="任意子阵营", options=FACTIONS),
            _input("类别", "菜单", default="步兵", options=UNIT_GROUPS),
            _input("具体单位", "单位菜单", default="任意", source="单位"),
        )),
        _block("单位.最近敌人", "选择最近的敌方单位", "报告器", output="目标引用"),
        _block("单位.最高威胁", "选择最高威胁敌人", "报告器", output="目标引用"),
        _block("单位.空闲", "选择我方空闲单位", "报告器", output="单位集合"),
        _block("单位.受伤", "选择我方受伤单位", "报告器", output="单位集合"),
        _block("单位.可部署", "选择可部署单位", "报告器", output="单位集合"),
        _block("单位.可装载", "选择可装载单位", "报告器", output="单位集合"),
        _block("单位.刚生产", "刚刚生产的单位", "报告器", output="单位集合"),
        _block("单位.刚征召", "刚刚征召的队伍", "报告器", output="队伍引用"),
        _block("单位.指定编队", "选择指定编队", "报告器", output="队伍引用", inputs=(_input("编队", "菜单", options=("进攻队", "防御队", "预备队", "侦察队", "护矿队", "空军队", "海军队")),)),
    ]},
    {"name_zh": "建筑", "color": "#9966ff", "blocks": [
        _block("建筑.选择", "选择建筑", "报告器", output="建筑集合", editor="级联建筑选择器", inputs=(
            _input("关系", "菜单", default="我方", options=RELATIONS),
            _input("子阵营", "菜单", default="任意子阵营", options=FACTIONS),
            _input("类别", "菜单", default="电厂", options=BUILDING_GROUPS),
            _input("具体建筑", "单位菜单", default="任意", source="建筑"),
        )),
        _block("建筑.缺少插件", "选择缺少插件的建筑", "报告器", output="建筑集合"),
        _block("建筑.受损", "选择受损建筑", "报告器", output="建筑集合"),
        _block("建筑.防御", "选择敌方防御建筑", "报告器", output="目标引用"),
        _block("建筑.生产", "选择敌方生产建筑", "报告器", output="目标引用"),
        _block("建筑.科技", "选择敌方科技建筑", "报告器", output="目标引用"),
        _block("建筑.电厂", "选择敌方电厂", "报告器", output="目标引用"),
        _block("建筑.基地车", "选择敌方基地车", "报告器", output="目标引用"),
        _block("建筑.中立科技", "选择未占领的中立科技建筑", "报告器", output="目标引用"),
        _block("建筑.可放置位置", "寻找建筑可放置位置", "报告器", output="位置引用", inputs=(_input("建筑", "建筑集合"),)),
        _block("建筑.插件覆盖位", "寻找插件覆盖位置", "报告器", output="位置引用", inputs=(_input("插件", "建筑集合"), _input("目标建筑", "建筑集合"))),
    ]},
    {"name_zh": "变量与列表", "color": "#ff8c1a", "blocks": [
        _block("变量.取得", "变量的值", "报告器", output="任意值", inputs=(_input("变量", "菜单", source="变量"),)),
        _block("变量.设置", "将变量设为", "命令", inputs=(_input("变量", "菜单", source="变量"), _input("值", "任意值"))),
        _block("变量.增加", "将变量增加", "命令", inputs=(_input("变量", "菜单", source="变量"), _input("数量", "数字", default=1))),
        _block("列表.加入", "将项目加入列表", "命令", inputs=(_input("项目", "任意值"), _input("列表", "菜单", source="列表"))),
        _block("列表.删除", "删除列表中的项目", "命令", inputs=(_input("项目", "任意值"), _input("列表", "菜单", source="列表"))),
        _block("列表.清空", "清空列表", "命令", inputs=(_input("列表", "菜单", source="列表"),)),
        _block("列表.数量", "列表项目数量", "报告器", output="数字", inputs=(_input("列表", "菜单", source="列表"),)),
        _block("列表.首项", "列表第一个项目", "报告器", output="任意值", inputs=(_input("列表", "菜单", source="列表"),)),
        _block("列表.包含", "列表包含项目吗", "布尔", output="布尔", inputs=(_input("列表", "菜单", source="列表"), _input("项目", "任意值"))),
        _block("列表.遍历", "遍历列表", "容器", inputs=(_input("列表", "菜单", source="列表"),), stacks=("对每个项目",)),
    ]},
    {"name_zh": "经济", "color": "#0fbd8c", "blocks": [
        _block("经济.现金高于", "现金高于", "布尔", output="布尔", inputs=(_input("资金", "数字", default=5000),)),
        _block("经济.收入为正", "收入正在增长吗", "布尔", output="布尔"),
        _block("经济.维持底线", "预计能维持资金底线吗", "布尔", output="布尔", inputs=(_input("资金底线", "数字", default=5000),)),
        _block("经济.矿区衰减", "基地矿区已经衰减吗", "布尔", output="布尔"),
        _block("经济.安全矿区", "存在安全的新矿区吗", "布尔", output="布尔"),
        _block("经济.分配预算", "分配生产预算", "命令", inputs=(_input("产线", "菜单", options=("建筑", "支援建筑", "步兵", "装甲", "飞机", "海军")), _input("比例", "数字", default=25))),
        _block("经济.暂停生产", "暂停低优先级生产", "命令"),
        _block("经济.恢复生产", "恢复独立产线生产", "命令"),
        _block("经济.保护矿区", "建立护矿预算", "命令", inputs=(_input("资金", "数字", default=1500),)),
    ]},
    {"name_zh": "队伍", "color": "#8b63ce", "blocks": [
        _block("队伍.征召", "征召单位", "命令", inputs=(_input("单位", "单位集合"), _input("数量", "数字", default=10))),
        _block("队伍.按比例组成", "按比例组成队伍", "命令", inputs=(_input("单位一", "单位集合"), _input("比例一", "数字", default=2), _input("单位二", "单位集合"), _input("比例二", "数字", default=1))),
        _block("队伍.进攻队", "建立进攻队", "命令", inputs=(_input("单位", "单位集合"),)),
        _block("队伍.防御队", "建立防御队", "命令", inputs=(_input("单位", "单位集合"),)),
        _block("队伍.预备队", "建立预备队", "命令", inputs=(_input("单位", "单位集合"),)),
        _block("队伍.侦察队", "建立侦察队", "命令", inputs=(_input("单位", "单位集合"),)),
        _block("队伍.补员", "补员到指定数量", "命令", inputs=(_input("队伍", "队伍引用"), _input("数量", "数字", default=20))),
        _block("队伍.集中", "集中队伍", "命令", inputs=(_input("队伍", "队伍引用"),)),
        _block("队伍.分散", "分散队伍", "命令", inputs=(_input("队伍", "队伍引用"), _input("间距", "数字", default=2))),
        _block("队伍.重组", "重新编队", "命令", inputs=(_input("队伍", "队伍引用"),)),
    ]},
    {"name_zh": "行动", "color": "#4c97ff", "blocks": [
        _block("行动.移动", "移动到", "命令", inputs=(_input("队伍", "队伍引用"), _input("位置", "位置引用")), executor="unit.move"),
        _block("行动.攻击", "攻击目标", "命令", inputs=(_input("队伍", "队伍引用"), _input("目标", "目标引用")), executor="unit.attack"),
        _block("行动.攻击移动", "边移动边攻击", "命令", inputs=(_input("队伍", "队伍引用"), _input("位置", "位置引用")), executor="unit.attack_move"),
        _block("行动.部署", "部署单位", "命令", inputs=(_input("单位", "单位集合"),), executor="unit.deploy"),
        _block("行动.解除部署", "解除部署", "命令", inputs=(_input("单位", "单位集合"),), executor="unit.undeploy"),
        _block("行动.装载", "装载进入运输单位", "命令", inputs=(_input("乘客", "单位集合"), _input("运输单位", "单位集合")), executor="unit.load"),
        _block("行动.卸载", "卸载运输单位", "命令", inputs=(_input("运输单位", "单位集合"), _input("位置", "位置引用")), executor="unit.unload"),
        _block("行动.生产", "生产单位", "命令", inputs=(_input("单位", "单位集合"), _input("数量", "数字", default=1)), executor="production.unit"),
        _block("行动.建造", "建造建筑", "命令", inputs=(_input("建筑", "建筑集合"), _input("位置", "位置引用")), executor="production.building"),
        _block("行动.安装插件", "安装插件到建筑", "命令", inputs=(_input("插件", "建筑集合"), _input("目标建筑", "建筑集合")), executor="production.upgrade"),
        _block("行动.维修", "维修对象", "命令", inputs=(_input("对象", "对象引用"),), executor="unit.repair"),
        _block("行动.占领", "占领中立建筑", "命令", inputs=(_input("工程师", "单位集合"), _input("建筑", "目标引用")), executor="unit.capture"),
        _block("行动.撤退", "撤退到安全位置", "命令", inputs=(_input("队伍", "队伍引用"), _input("位置", "位置引用")), executor="unit.retreat"),
        _block("行动.释放控制", "释放单位控制权", "命令", inputs=(_input("单位", "单位集合"),), executor="unit.release"),
    ]},
    {"name_zh": "空间与目标", "color": "#4cbfe6", "blocks": [
        _block("空间.基地中心", "基地中心位置", "报告器", output="位置引用"),
        _block("空间.最近矿区", "最近的安全矿区", "报告器", output="位置引用"),
        _block("空间.安全位置", "最近的安全位置", "报告器", output="位置引用", inputs=(_input("对象", "对象引用"),)),
        _block("空间.集结点", "队伍集结点", "报告器", output="位置引用", inputs=(_input("队伍", "队伍引用"),)),
        _block("空间.敌方基地", "敌方基地位置", "报告器", output="位置引用"),
        _block("空间.目标位置", "目标当前位置", "报告器", output="位置引用", inputs=(_input("目标", "目标引用"),)),
        _block("空间.最近目标", "选择最近目标", "报告器", output="目标引用", inputs=(_input("候选目标", "对象引用"),)),
        _block("空间.最高价值", "选择最高价值目标", "报告器", output="目标引用", inputs=(_input("候选目标", "对象引用"),)),
        _block("空间.避开威胁", "规划避开威胁的路径", "报告器", output="路径引用", inputs=(_input("起点", "位置引用"), _input("终点", "位置引用"))),
        _block("空间.距离", "两个对象的距离", "报告器", output="数字", inputs=(_input("对象一", "对象引用"), _input("对象二", "对象引用"))),
    ]},
    {"name_zh": "克制", "color": "#ff6680", "blocks": [
        _block("克制.反步兵", "需要反步兵吗", "布尔", output="布尔"),
        _block("克制.反装甲", "需要反装甲吗", "布尔", output="布尔"),
        _block("克制.反空军", "需要反空军吗", "布尔", output="布尔"),
        _block("克制.反海军", "需要反海军吗", "布尔", output="布尔"),
        _block("克制.火力有效", "我方火力对目标有效吗", "布尔", output="布尔", inputs=(_input("目标", "目标引用"),)),
        _block("克制.范围攻击", "敌方拥有范围反步兵火力吗", "布尔", output="布尔"),
        _block("克制.敌方空投", "敌方正在空投吗", "布尔", output="布尔"),
        _block("克制.兵力优势", "兵力优势允许总攻吗", "布尔", output="布尔", inputs=(_input("优势比例", "数字", default=1.25),)),
        _block("克制.选择反制单位", "选择最合适的反制单位", "报告器", output="单位集合", inputs=(_input("目标", "目标引用"),)),
        _block("克制.切换方案", "切换到反制策略", "命令", inputs=(_input("威胁类型", "菜单", options=(
            "步兵海", "精英步兵", "范围反步兵", "轻装甲", "重装甲", "炮兵", "攻城单位",
            "空军群", "轰炸机突袭", "直升机袭击", "防空网", "隐形渗透", "水面舰队",
            "潜艇", "两栖登陆", "基地防御", "超级武器", "经济扩张", "科技速攻",
            "早期突袭", "矿车袭击", "运输空降", "心灵控制", "综合部队",
        )),)),
    ]},
    {"name_zh": "战术设置", "color": "#e45757", "blocks": [
        _block("战术.策略优先级", "将本策略优先级设为", "命令", inputs=(_input("优先级", "数字", default=100),)),
        _block("战术.最低触发数量", "敌军达到多少时触发", "命令", inputs=(_input("数量", "数字", default=1),)),
        _block("战术.最低威胁值", "敌军威胁值达到多少时触发", "命令", inputs=(_input("威胁值", "数字", default=20),)),
        _block("战术.最低集结数量", "我方至少集结多少单位", "命令", inputs=(_input("数量", "数字", default=8),)),
        _block("战术.设置姿态", "将作战姿态设为", "命令", inputs=(_input("姿态", "菜单", options=("防守", "迟滞", "试探", "施压", "进攻", "决战")),)),
        _block("战术.生产角色", "生产对应角色单位", "命令", inputs=(_input("角色", "文字输入"),)),
        _block("战术.目标优先级", "将目标列为第几优先", "命令", inputs=(_input("目标类型", "文字输入"), _input("顺序", "数字", default=1))),
        _block("战术.指定目标优先级", "将指定目标列为第几优先", "命令", inputs=(_input("目标", "对象引用"), _input("顺序", "数字", default=1))),
        _block("战术.设置阵型", "将队伍阵型设为", "命令", inputs=(_input("阵型", "文字输入"),)),
        _block("战术.设置交战方式", "将交战方式设为", "命令", inputs=(_input("方式", "文字输入"),)),
        _block("战术.设置支援时机", "设置支援技能释放时机", "命令", inputs=(_input("时机", "文字输入"),)),
        _block("战术.设置前线比例", "设置前线部队比例", "命令", inputs=(_input("百分比", "数字", default=60),)),
        _block("战术.设置反装甲比例", "设置反装甲部队比例", "命令", inputs=(_input("百分比", "数字", default=20),)),
        _block("战术.设置防空比例", "设置防空部队比例", "命令", inputs=(_input("百分比", "数字", default=15),)),
        _block("战术.设置预备队比例", "设置预备队比例", "命令", inputs=(_input("百分比", "数字", default=25),)),
        _block("战术.设置交战距离", "设置交战距离", "命令", inputs=(_input("地图格", "数字", default=8),)),
        _block("战术.设置追击半径", "设置最大追击半径", "命令", inputs=(_input("地图格", "数字", default=10),)),
        _block("战术.设置撤退生命", "设置撤退生命值", "命令", inputs=(_input("百分比", "数字", default=35),)),
        _block("战术.设置重组时间", "设置重组等待时间", "命令", inputs=(_input("秒数", "数字", default=10),)),
        _block("战术.压制盟军机动", "压制盟军机动与空军", "命令"),
        _block("战术.拉扯苏军装甲", "拉扯苏军装甲与火炮", "命令"),
        _block("战术.防范心灵隐形", "防范心灵控制与隐形", "命令"),
        _block("战术.拆解焚风协同", "拆解焚风支援协同", "命令"),
        _block("战术.侦察识别", "侦察并识别未知敌军", "命令"),
        _block("战术.保护开局经济", "保护开局经济", "命令"),
        _block("战术.争夺地图", "争夺地图并解锁反制科技", "命令"),
        _block("战术.组建混编部队", "组建混合兵种部队", "命令"),
        _block("战术.轮换进攻波次", "轮换并补充进攻波次", "命令"),
        _block("战术.发起决战", "发起决定性进攻", "命令"),
        _block("战术.经济型反制", "采用高性价比反制", "命令"),
        _block("战术.维持反制比例", "维持反制部队比例", "命令"),
        _block("战术.并行生产反制", "并行生产强力反制单位", "命令"),
        _block("战术.迟滞补员", "迟滞敌军并补充兵力", "命令"),
        _block("战术.试探后投入", "试探后投入主力", "命令"),
        _block("战术.扩大优势", "确认优势后持续施压", "命令"),
        _block("战术.侧翼与间距", "利用侧翼并保持单位间距", "命令"),
        _block("战术.控制狭窄口", "使用射程和范围火力控制狭窄口", "命令"),
        _block("战术.目标消失重判", "目标类型消失后重新判断", "命令"),
    ]},
    {"name_zh": "安全约束", "color": "#7c6f9f", "blocks": [
        _block("约束.只用可见信息", "只使用已经看见的战场信息", "命令"),
        _block("约束.尊重玩家控制", "保持玩家拥有最终控制权", "命令"),
        _block("约束.产线独立", "保持六条生产线独立运行", "命令"),
        _block("约束.验证动作", "每个游戏动作都必须验证结果", "命令"),
        _block("约束.确认目标类型", "下令前再次确认目标类型", "命令"),
        _block("约束.禁止战中换乘", "接战期间禁止切换步战车乘员", "命令"),
        _block("约束.矿车不参战", "禁止采矿车加入战斗编组", "命令"),
        _block("约束.飞机补弹释放", "战机补弹期间释放控制权", "命令"),
    ]},
    {"name_zh": "支援", "color": "#d65cd6", "blocks": [
        _block("支援.可用", "支援技能可以使用吗", "布尔", output="布尔", inputs=(_input("技能", "菜单", options=("蜂鸟支援机", "空袭", "超时空传送", "铁幕", "侦察扫描", "超级武器")),)),
        _block("支援.目标密集", "目标区域足够密集吗", "布尔", output="布尔", inputs=(_input("目标", "目标引用"), _input("最少数量", "数字", default=5))),
        _block("支援.蜂鸟", "释放蜂鸟支援机", "命令", inputs=(_input("目标", "目标引用"),)),
        _block("支援.空袭", "释放空袭", "命令", inputs=(_input("目标", "目标引用"),)),
        _block("支援.超时空", "释放超时空传送", "命令", inputs=(_input("队伍", "队伍引用"), _input("位置", "位置引用"))),
        _block("支援.铁幕", "释放铁幕", "命令", inputs=(_input("队伍", "队伍引用"),)),
        _block("支援.超级武器", "释放超级武器", "命令", inputs=(_input("目标", "目标引用"),)),
    ]},
    {"name_zh": "验证", "color": "#59c059", "blocks": [
        _block("验证.已到达", "已经到达吗", "布尔", output="布尔", inputs=(_input("对象", "对象引用"), _input("位置", "位置引用"))),
        _block("验证.已击毁", "目标已经击毁吗", "布尔", output="布尔", inputs=(_input("目标", "目标引用"),)),
        _block("验证.已生产", "生产已经完成吗", "布尔", output="布尔", inputs=(_input("单位", "单位集合"), _input("数量", "数字", default=1))),
        _block("验证.已建造", "建筑已经建造吗", "布尔", output="布尔", inputs=(_input("建筑", "建筑集合"),)),
        _block("验证.插件覆盖", "插件覆盖成功吗", "布尔", output="布尔", inputs=(_input("目标建筑", "建筑集合"),)),
        _block("验证.已装载", "装载已经完成吗", "布尔", output="布尔", inputs=(_input("乘客", "单位集合"), _input("运输单位", "单位集合"))),
        _block("验证.已部署", "单位已经部署吗", "布尔", output="布尔", inputs=(_input("单位", "单位集合"),)),
        _block("验证.维修完成", "维修已经完成吗", "布尔", output="布尔", inputs=(_input("对象", "对象引用"),)),
        _block("验证.动作成功", "上一个动作成功吗", "布尔", output="布尔"),
    ]},
    {"name_zh": "学习", "color": "#cf63cf", "blocks": [
        _block("学习.记录战损", "记录本次战损", "命令"),
        _block("学习.记录收益", "记录本次收益", "命令"),
        _block("学习.记录耗时", "记录执行耗时", "命令"),
        _block("学习.记录失败", "记录失败原因", "命令", inputs=(_input("原因", "文字", default=""),)),
        _block("学习.记录环境", "记录适用环境", "命令"),
        _block("学习.试用临时", "将方案标记为临时试用", "命令"),
        _block("学习.批准长期", "将方案提交为长期策略", "命令"),
    ]},
)


def block_registry() -> dict[str, Any]:
    return {
        "schema": "mo.scratch.registry",
        "schema_version": 2,
        "groups": deepcopy(BLOCK_GROUPS),
        "connection_types": {
            "对象引用": ["单位引用", "单位集合", "队伍引用", "建筑引用", "建筑集合", "目标引用", "位置引用"],
            "目标引用": ["单位引用", "单位集合", "队伍引用", "建筑引用", "建筑集合", "目标引用"],
            "单位集合": ["单位引用", "单位集合", "队伍引用"],
            "建筑集合": ["建筑引用", "建筑集合"],
            "任意值": ["数字", "文字", "布尔", "对象引用", "单位引用", "单位集合", "队伍引用", "建筑引用", "建筑集合", "目标引用", "位置引用", "路径引用"],
        },
    }


def block_by_id(identifier: str) -> dict[str, Any] | None:
    for group in BLOCK_GROUPS:
        for block in group["blocks"]:
            if block["id"] == identifier:
                value = deepcopy(block)
                value["group_zh"] = group["name_zh"]
                value["color"] = group["color"]
                return value
    return None


def block_index() -> dict[str, dict[str, Any]]:
    return {
        block["id"]: {**deepcopy(block), "group_zh": group["name_zh"], "color": group["color"]}
        for group in BLOCK_GROUPS
        for block in group["blocks"]
    }
