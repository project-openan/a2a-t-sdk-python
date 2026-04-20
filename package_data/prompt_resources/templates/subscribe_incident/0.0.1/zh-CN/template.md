## 订阅指令
请根据以下 <事件主题>、<订阅条件>、<上报事件数据格式>、及<预期输出> 信息，完成自治数据的事件订阅与上报任务。
其中<事件主题>表示需要订阅的主题内容或事件类型。
<订阅条件>表示触发事件上报的指标、阈值或者触发上报的方式。
<上报事件数据格式>表示上报数据的结构与字段要求。
<预期输出>表示订阅执行的结果及返回内容。

## 事件主题
主题名称：incident

## 订阅条件
要求：当前订阅条件包括 “故障名称”、“故障级别”。均为可选参数。
故障名称参数支持传入列表，该参数的取值范围是网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障，光纤接头脏污，网元掉电，环境温度异常，业务版故障，风扇板故障，NPE到核心网路由不通，激光器老化，PWE3 QoS限速，MPLS-TP静态隧道限速，SR-TP静态隧道限速，专线接入端口限速，带宽利用率过高等。
故障级别参数支持传入列表，该参数的取值范围包括 critical（严重）、major（高）、minor（中）、warning（低）。

故障名称为 {subscription_condition_incident_name}；
故障级别为 {subscription_condition_incident_level}

## 上报事件数据格式
要求：按照以下 schema 链接中的schema和目标模型的定义进行事件数据上报。
模型定义schema： https://projects.tmforum.org/a2aproject/telecommunication/extensions/faultManagement/Incident/v1
模型名称：IncidentObject

## 预期输出
1、订阅结果：成功或失败；
2、订阅失败原因（可选）