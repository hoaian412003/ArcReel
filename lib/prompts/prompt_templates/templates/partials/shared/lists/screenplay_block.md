## 已确认剧本

<screenplay>
标题：{{ screenplay.title }}
看点：{{ screenplay.logline }}

{% for scene in screenplay.scenes %}
### 第 {{ loop.index }} 场　{{ scene.heading }}（预计 {{ scene.estimated_seconds }} 秒）
{{ scene.summary }}

{{ scene.text }}

{% endfor %}
</screenplay>

### 如何使用剧本与源文

上方剧本的情节、人物与台词已经由编剧定稿并经人工确认；你**不新增情节、不改写已确认的台词**，只负责划分单元、选择时长、登记出场资产、锚定源文原句。

- 剧本决定**怎么分**：场次顺序、每一场发生什么、整体节奏与时长预算。
