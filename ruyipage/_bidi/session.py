# -*- coding: utf-8 -*-
"""BiDi session 模块命令"""


def status(driver):
    """查询远程端状态

    Returns:
        {'ready': bool, 'message': str}
    """
    return driver.run("session.status")


def new(driver, capabilities=None, user_prompt_handler=None):
    """创建新会话

    Args:
        capabilities: 能力请求字典
        user_prompt_handler: 可选，session.UserPromptHandler 字典

    Returns:
        {'sessionId': str, 'capabilities': dict}
    """
    caps = dict(capabilities or {})
    if user_prompt_handler:
        always_match = dict(caps.get("alwaysMatch", {}))
        always_match["unhandledPromptBehavior"] = dict(user_prompt_handler)
        caps["alwaysMatch"] = always_match
    params = {"capabilities": caps}
    return driver.run("session.new", params)


def end(driver):
    """结束当前会话"""
    return driver.run("session.end")


def subscribe(driver, events, contexts=None, user_contexts=None):
    """订阅事件

    Args:
        events: 事件名列表，如 ['network.responseCompleted', 'log.entryAdded']
                也可以是模块名，如 ['network'] 订阅该模块所有事件
        contexts: 可选，限定 context 列表

    Returns:
        {'subscription': str}  订阅 ID
    """
    params = {"events": events if isinstance(events, list) else [events]}
    if contexts and user_contexts:
        raise ValueError("contexts and user_contexts cannot both be provided")
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    if user_contexts:
        params["userContexts"] = user_contexts if isinstance(user_contexts, list) else [user_contexts]
    return driver.run("session.subscribe", params)


def subscribe_compatible(driver, events, contexts=None):
    """兼容订阅事件，避免一个不支持的事件拖垮整批订阅。

    Firefox / BiDi 版本之间存在事件名支持差异。某些版本会因为列表中
    任意一个未知事件拒绝整个 ``session.subscribe``，因此批量失败后逐个
    重试，保留可用事件。

    Returns:
        dict: ``events`` 为成功订阅的事件列表，``failed_events`` 为
        ``[(event, exc), ...]``，``subscription`` 可直接传给 unsubscribe。
    """
    event_list = list(events) if isinstance(events, (list, tuple)) else [events]
    if not event_list:
        return {
            "subscription": None,
            "subscriptions": [],
            "events": [],
            "failed_events": [],
        }

    try:
        result = subscribe(driver, event_list, contexts=contexts)
        subscription = result.get("subscription")
        return {
            "subscription": subscription,
            "subscriptions": [subscription] if subscription else [],
            "events": event_list,
            "failed_events": [],
            "raw": result,
        }
    except Exception as batch_error:
        subscriptions = []
        accepted_events = []
        failed_events = []

        for event in event_list:
            try:
                result = subscribe(driver, [event], contexts=contexts)
                subscription = result.get("subscription")
                if subscription:
                    subscriptions.append(subscription)
                accepted_events.append(event)
            except Exception as event_error:
                failed_events.append((event, event_error))

        if not accepted_events:
            raise batch_error

        subscription = subscriptions[0] if len(subscriptions) == 1 else subscriptions
        return {
            "subscription": subscription,
            "subscriptions": subscriptions,
            "events": accepted_events,
            "failed_events": failed_events,
            "batch_error": batch_error,
        }


def unsubscribe(driver, events=None, contexts=None, subscription=None):
    """取消订阅事件

    Args:
        events: 事件名列表
        contexts: 可选，限定 context 列表
        subscription: 可选，通过订阅 ID 取消
    """
    params = {}
    if subscription:
        params["subscriptions"] = (
            [subscription] if isinstance(subscription, str) else subscription
        )
    else:
        if events:
            params["events"] = events if isinstance(events, list) else [events]
    return driver.run("session.unsubscribe", params)
