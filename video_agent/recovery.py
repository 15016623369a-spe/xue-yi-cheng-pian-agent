"""Bounded recovery decisions. Retry transport/tools, never invent missing inputs."""
from __future__ import annotations


def decision(error,kind=''):
    message=str(error);low=message.lower()
    if any(t in low for t in ['http 401','http 403','api key','api_key','证书','tls','验证码','登录','cookie','付费']):
        return {'code':'access_or_settings','retry':False,'action':'请核对模型设置、登录条件或素材访问权限，再重新开始。'}
    if any(t in low for t in ['http 402','额度','余额不足']):
        return {'code':'quota','retry':False,'action':'请检查服务商额度或选择其他已配置的模型。'}
    if any(t in low for t in ['anchor must occur','触发句','唯一完全匹配','原话至少']):
        return {'code':'anchor_needs_input','retry':False,'action':'无法唯一定位触发句；请使用本镜口播中的原句，或手动核对片头剪点。'}
    if any(t in low for t in ['missing asset','missing voice','no such file','未配置','请先配置','不存在','未登记']):
        return {'code':'missing_input','retry':False,'action':'请补充对应的文件、素材或本机配置；已有成功步骤会保留。'}
    if kind in {'render','cover','opening'} and any(t in low for t in ['session closed','target closed','browser crashed','target-closed','render stopped:','scene deadline exceeded','connection reset','temporarily unavailable','超时','网络连接失败']):
        return {'code':'temporary_tool_failure','retry':True,'action':'自动重新启动这一步，复用已通过检查的配音、画面及下载结果。'}
    if any(t in low for t in ['speech phrase','asr/script','配音','未读完','漏读']):
        return {'code':'speech_needs_review','retry':False,'action':'本地配音已有限尝试仍未通过，请检查这一镜的术语读法或参考声音。'}
    return {'code':'needs_review','retry':False,'action':'自动修复未能确定可靠结果；请查看具体错误并修正对应输入。'}
