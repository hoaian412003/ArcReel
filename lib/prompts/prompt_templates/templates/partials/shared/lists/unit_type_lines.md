{% for item in unit_types %}
- `{{ item.value }}`：{{ item.meaning }}
{% endfor %}