import logging
import platform
import threading
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

from ._debug import info as debug_info  # noqa: F401
from ._version import __author__, __version__  # noqa: F401
from .exceptions import NoValidDisplayError, format_exc
from .helpers import MONITOR_MANUFACTURER_CODES, Display, percentage  # noqa: F401
from .helpers import BrightnessMethod, ScreenBrightnessError, logarithmic_range
from dataclasses import fields

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def get_brightness(
    display: Optional[Union[int, str]] = None,
    method: Optional[str] = None,
    verbose_error: bool = False
) -> List[int]:
    '''
    Returns the current display brightness

    Args:
        display (str or int): the specific display to query
        method (str): the method to use to get the brightness. See `get_methods` for
            more info on available methods
        verbose_error (bool): controls the level of detail in the error messages

    Returns:
        list: a list of integers (from 0 to 100), each integer being the
            percentage brightness of a display (invalid displays may return None)

    Example:
        ```python
        import screen_brightness_control as sbc

        # get the current screen brightness (for all detected displays)
        current_brightness = sbc.get_brightness()

        # get the brightness of the primary display
        primary_brightness = sbc.get_brightness(display=0)

        # get the brightness of the secondary display (if connected)
        secondary_brightness = sbc.get_brightness(display=1)
        ```
    '''
    return __brightness(display=display, method=method, meta_method='get', verbose_error=verbose_error)


def set_brightness(
    value: Union[int, float, str],
    display: Optional[Union[str, int]] = None,
    method: Optional[str] = None,
    force: bool = False,
    verbose_error: bool = False,
    no_return: bool = True
) -> Union[None, List[int]]:
    '''
    Sets the screen brightness

    Args:
        value (int or float or str): a value 0 to 100. This is a percentage or a string as '+5' or '-5'
        display (int or str): the specific display to adjust
        method (str): the method to use to set the brightness. See `get_methods` for
            more info on available methods
        force (bool): [*Linux Only*] if False the brightness will never be set lower than 1.
            This is because on most displays a brightness of 0 will turn off the backlight.
            If True, this check is bypassed
        verbose_error (bool): boolean value controls the amount of detail error messages will contain
        no_return (bool): if False, this function returns new brightness (by calling `get_brightness`).
            If True, this function returns None (default behaviour).

    Returns:
        None: if the `no_return` kwarg is `True`
        list: a list of integers (from 0 to 100), each integer being the
            percentage brightness of a display (invalid displays may return None)

    Example:
        ```python
        import screen_brightness_control as sbc

        # set brightness to 50%
        sbc.set_brightness(50)

        # set brightness to 0%
        sbc.set_brightness(0, force=True)

        # increase brightness by 25%
        sbc.set_brightness('+25')

        # decrease brightness by 30%
        sbc.set_brightness('-30')

        # set the brightness of display 0 to 50%
        sbc.set_brightness(50, display=0)
        ```
    '''
    if isinstance(value, str) and ('+' in value or '-' in value):
        output = []
        for monitor in filter_monitors(display=display, method=method):
            identifier = Monitor.get_identifier(monitor)[1]
            current_value = get_brightness(display=identifier)[0]
            result = set_brightness(
                # don't need to calculate lower bound here because it will be
                # done by the other path in `set_brightness`
                percentage(value, current=current_value),
                display=identifier,
                force=force,
                verbose_error=verbose_error,
                no_return=no_return
            )
            if result is None:
                output.append(result)
            else:
                output += result

        return output

    if platform.system() == 'Linux' and not force:
        lower_bound = 1
    else:
        lower_bound = 0

    value = percentage(value, lower_bound=lower_bound)

    return __brightness(
        value, display=display, method=method,
        meta_method='set', no_return=no_return,
        verbose_error=verbose_error
    )


def fade_brightness(
    finish: Union[int, str],
    start: Optional[Union[int, str]] = None,
    interval: float = 0.01,
    increment: int = 1,
    blocking: bool = True,
    force: bool = False,
    logarithmic: bool = True,
    **kwargs
) -> Union[List[threading.Thread], List[int]]:
    '''
    Gradually change the brightness of one or more displays

    Args:
        finish (int or str): the brightness level to end up on
        start (int or str): where the brightness should fade from.
            If not specified the function starts from the current brightness
        interval (float or int): the time delay between each step in brightness
        increment (int): the amount to change the brightness by per step
        blocking (bool): whether this should occur in the main thread (`True`) or a new daemonic thread (`False`)
        force (bool): [*Linux Only*] if False the brightness will never be set lower than 1.
            This is because on most displays a brightness of 0 will turn off the backlight.
            If True, this check is bypassed
        logarithmic (bool): follow a logarithmic brightness curve when adjusting the brightness
        kwargs (dict): passed directly to `set_brightness`.
            Any compatible kwargs are passed to `filter_monitors` as well. (eg: display, method...)

    Returns:
        list: list of `threading.Thread` objects if `blocking == False`,
            otherwise it returns the result of `get_brightness()`

    Example:
        ```python
        import screen_brightness_control as sbc

        # fade brightness from the current brightness to 50%
        sbc.fade_brightness(50)

        # fade the brightness from 25% to 75%
        sbc.fade_brightness(75, start=25)

        # fade the brightness from the current value to 100% in steps of 10%
        sbc.fade_brightness(100, increment=10)

        # fade the brightness from 100% to 90% with time intervals of 0.1 seconds
        sbc.fade_brightness(90, start=100, interval=0.1)

        # fade the brightness to 100% in a new thread
        sbc.fade_brightness(100, blocking=False)
        ```
    '''
    def fade(start, finish, increment, monitor):
        range_func = logarithmic_range if logarithmic else range

        increment = abs(increment)
        if start > finish:
            increment = -increment

        logger.debug(
            f'fade display {monitor.index} of {monitor.method}'
            f' {start}->{finish}:{increment}:logarithmic={logarithmic}'
        )
        for value in range_func(start, finish, increment):
            monitor.set_brightness(value, no_return=True)
            time.sleep(interval)

        if monitor.get_brightness() != finish:
            monitor.set_brightness(finish, no_return=True)

    # make sure only compatible kwargs are passed to filter_monitors
    available_monitors = filter_monitors(
        **{k: v for k, v in kwargs.items() if k in (
            'display', 'haystack', 'method', 'include'
        )}
    )

    # minimum brightness value
    if platform.system() == 'Linux' and not force:
        lower_bound = 1
    else:
        lower_bound = 0

    threads = []
    for i in available_monitors:
        try:
            monitor = Monitor(i)

            # same effect as monitor.is_active()
            current = monitor.get_brightness()
        except Exception as e:
            logger.error(f'exception when preparing to fade monitor {i} - {format_exc(e)}')
            continue

        st, fi = start, finish
        # convert strings like '+5' to an actual brightness value
        if isinstance(fi, str):
            if "+" in fi or "-" in fi:
                fi = current + int(float(fi))
        if isinstance(st, str):
            if "+" in st or "-" in st:
                st = current + int(float(st))

        st = current if st is None else st
        # make sure both values are within the correct range
        fi = min(max(int(float(fi)), lower_bound), 100)
        st = min(max(int(float(st)), lower_bound), 100)

        t1 = threading.Thread(target=fade, args=(st, fi, increment, monitor))
        t1.start()
        threads.append(t1)

    if not blocking:
        return threads

    for t in threads:
        t.join()
    return get_brightness(**kwargs)


def list_monitors_info(
    method: Optional[str] = None, allow_duplicates: bool = False, unsupported: bool = False
) -> List[dict]:
    '''
    List detailed information about all displays that are controllable by this library

    Args:
        method (str): the method to use to list the available displays. See `get_methods` for
            more info on available methods
        allow_duplicates (bool): whether to filter out duplicate displays or not
        unsupported (bool): include detected displays that are invalid or unsupported

    Returns:
        list: list of dictionaries

    Example:
        ```python
        import screen_brightness_control as sbc
        displays = sbc.list_monitors_info()
        for display in displays:
            print('=======================')
            # the manufacturer name plus the model
            print('Name:', display['name'])
            # the general model of the display
            print('Model:', display['model'])
            # the serial of the display
            print('Serial:', display['serial'])
            # the name of the brand of the display
            print('Manufacturer:', display['manufacturer'])
            # the 3 letter code corresponding to the brand name, EG: BNQ -> BenQ
            print('Manufacturer ID:', display['manufacturer_id'])
            # the index of that display FOR THE SPECIFIC METHOD THE DISPLAY USES
            print('Index:', display['index'])
            # the method this display can be addressed by
            print('Method:', display['method'])
            # the EDID string associated with that display
            print('EDID:', display['edid'])
        ```
    '''
    return _OS_MODULE.list_monitors_info(
        method=method, allow_duplicates=allow_duplicates, unsupported=unsupported
    )


def list_monitors(method: Optional[str] = None) -> List[str]:
    '''
    List the names of all detected displays

    Args:
        method (str): the method to use to list the available displays. See `get_methods` for
            more info on available methods

    Returns:
        list: list of strings

    Example:
        ```python
        import screen_brightness_control as sbc
        display_names = sbc.list_monitors()
        # eg: ['BenQ GL2450H', 'Dell U2211H']
        ```
    '''
    return [i['name'] for i in list_monitors_info(method=method)]


def get_methods(name: str = None) -> Dict[str, BrightnessMethod]:
    '''
    Returns all available brightness method names and their associated classes.

    Args:
        name (str): if specified, return the method corresponding to this name

    Returns:
        dict: keys are the method names. This is what you would use
            if a function has a `method` kwarg.
            Values are the classes themselves

    Raises:
        ValueError: if the given name is incorrect

    Example:
        ```python
        import screen_brightness_control as sbc

        all_methods = sbc.get_methods()

        for method_name, method_class in all_methods.items():
            print('Method:', method_name)
            print('Class:', method_class)
            print('Associated monitors:', sbc.list_monitors(method=method_name))
        ```
    '''
    methods = {i.__name__.lower(): i for i in _OS_METHODS}

    if name is None:
        return methods

    if not isinstance(name, str):
        raise TypeError(f'name must be of type str, not {type(name)!r}')

    name = name.lower()
    if name in methods:
        return {name: methods[name]}

    logger.debug(f'requested method {name!r} invalid')
    raise ValueError(
        f'invalid method {name!r}, must be one of: {list(methods)}')


class Monitor(Display):
    '''A class to manage a single monitor and its relevant information'''

    def __init__(self, display: Union[int, str, dict]):
        '''
        Args:
            display (int or str or dict): the index/name/model name/serial/edid
                of the display you wish to control. Is passed to `filter_monitors`
                to decide which display to use.

        Example:
            ```python
            import screen_brightness_control as sbc

            # create a class for the primary display and then a specifically named monitor
            primary = sbc.Monitor(0)
            benq_monitor = sbc.Monitor('BenQ GL2450H')

            # check if the benq monitor is the primary one
            if primary.serial == benq_monitor.serial:
                print('BenQ GL2450H is the primary display')
            else:
                print('The primary display is', primary.name)

            # DEPRECATED BEHAVIOUR
            # Will be removed in v0.22.0
            print(primary['name'])
            print(benq_monitor['name'])
            ```
        '''
        monitors_info = list_monitors_info(allow_duplicates=True)
        if isinstance(display, dict):
            if display in monitors_info:
                info = display
            else:
                info = filter_monitors(
                    display=self.get_identifier(display),
                    haystack=monitors_info
                )[0]
        else:
            info = filter_monitors(display=display, haystack=monitors_info)[0]

        # make a copy so that we don't alter the dict in-place
        info = info.copy()

        kw = [i.name for i in fields(Display) if i.init]
        super().__init__(**{k: v for k, v in info.items() if k in kw})

        # this assigns any extra info that is returned to this class
        # eg: the 'interface' key in XRandr monitors on Linux
        for key, value in info.items():
            if key not in kw and value is not None:
                setattr(self, key, value)

    def __getitem__(self, item: Any) -> Any:
        '''
        .. warning:: Deprecated
           This behaviour is deprecated and will be removed in v0.22.0
        '''
        return getattr(self, item)

    def get_identifier(self, monitor: dict = None) -> Tuple[str, Union[int, str]]:
        '''
        Returns the piece of information used to identify this display.
        Will iterate through the EDID, serial, name and index and return the first
        value that is not equal to None

        Args:
            monitor (dict): extract an identifier from this dict instead of the monitor class

        Returns:
            tuple: the name of the property returned and the value of said property.
                EG: `('serial', '123abc...')` or `('name', 'BenQ GL2450H')`

        Example:
            ```python
            import screen_brightness_control as sbc
            primary = sbc.Monitor(0)
            print(primary.get_identifier())  # eg: ('serial', '123abc...')

            secondary = sbc.list_monitors_info()[1]
            print(primary.get_identifier(monitor=secondary))  # eg: ('serial', '456def...')

            # you can also use the class uninitialized
            print(sbc.Monitor.get_identifier(secondary))  # eg: ('serial', '456def...')
            ```
        '''
        if monitor is None:
            if isinstance(self, dict):
                monitor = self
            else:
                return super().get_identifier()

        for key in ('edid', 'serial', 'name', 'index'):
            value = monitor[key]
            if value is not None:
                return key, value

    def set_brightness(self, value: Union[int, str], no_return: bool = True, force: bool = False) -> Union[None, int]:
        # refresh display info, in case another display has been unplugged or something
        # which would change the index of this display
        self.get_info()
        return super().set_brightness(value, no_return, force)

    def get_brightness(self) -> int:
        # refresh display info, in case another display has been unplugged or something
        # which would change the index of this display
        self.get_info()
        return super().get_brightness()

    def fade_brightness(
        self,
        *args,
        blocking: bool = True,
        **kwargs
    ) -> Union[threading.Thread, int]:
        '''
        Wrapper for `Display.fade_brightness`

        Args:
            *args: see `Display.fade_brightness`
            blocking (bool): run this function in the current thread and block until
                it completes. If `False`, the fade will be run in a new daemonic
                thread, which will be started and returned
            **kwargs: see `Display.fade_brightness`
        '''
        if not blocking:
            result = threading.Thread(
                target=super().fade_brightness, args=args, kwargs=kwargs, daemon=True)
            result.start()
        else:
            result = super().fade_brightness(*args, **kwargs)

        return result

    def get_info(self, refresh: bool = True) -> dict:
        '''
        Returns all known information about this monitor instance

        Args:
            refresh (bool): whether to refresh the information
                or to return the cached version

        Returns:
            dict

        Example:
            ```python
            import screen_brightness_control as sbc

            # initialize class for primary display
            primary = sbc.Monitor(0)
            # get the info
            info = primary.get_info()
            ```
        '''
        def vars_self():
            return {k: v for k, v in vars(self).items() if not k.startswith('_')}

        if not refresh:
            return vars_self()

        identifier = self.get_identifier()

        if identifier is not None:
            # refresh the info we have on this monitor
            info = filter_monitors(
                display=identifier[1], method=self.method.__name__)[0]
            for key, value in info.items():
                if value is not None:
                    setattr(self, key, value)

        return vars_self()


def filter_monitors(
    display: Optional[Union[int, str]] = None,
    haystack: Optional[List[dict]] = None,
    method: Optional[str] = None,
    include: List[str] = []
) -> List[dict]:
    '''
    Searches through the information for all detected displays
    and attempts to return the info matching the value given.
    Will attempt to match against index, name, model, edid, method and serial

    Args:
        display (str or int): the display you are searching for.
            Can be serial, name, model number, edid string or index of the display
        haystack (list): the information to filter from.
            If this isn't set it defaults to the return of `list_monitors_info`
        method (str): the method the monitors use. See `get_methods` for
            more info on available methods
        include (list): extra fields of information to sort by

    Raises:
        NoValidDisplayError: if the display does not have a match

    Returns:
        list: list of dicts

    Example:
        ```python
        import screen_brightness_control as sbc

        search = 'GL2450H'
        match = sbc.filter_displays(search)
        print(match)
        # EG output: [{'name': 'BenQ GL2450H', 'model': 'GL2450H', ... }]
        ```
    '''
    if display is not None and type(display) not in (str, int):
        raise TypeError(
            f'display kwarg must be int or str, not "{type(display).__name__}"')

    def get_monitor_list():
        # if we have been provided with a list of monitors to sift through then use that
        # otherwise, get the info ourselves
        if haystack is not None:
            monitors_with_duplicates = haystack
            if method is not None:
                method_class = next(get_methods(method).values())
                monitors_with_duplicates = [
                    i for i in haystack if i['method'] == method_class]
        else:
            monitors_with_duplicates = list_monitors_info(
                method=method, allow_duplicates=True)

        return monitors_with_duplicates

    def filter_monitor_list(to_filter):
        # This loop does two things:
        # 1. Filters out duplicate monitors
        # 2. Matches the display kwarg (if applicable)
        filtered_displays = {}
        for monitor in to_filter:
            # find a valid identifier for a monitor, excluding any which are equal to None
            added = False
            for identifier in ['edid', 'serial', 'name', 'model'] + include:
                # check we haven't already added the monitor
                if monitor.get(identifier, None) is None:
                    continue

                m_id = monitor[identifier]
                if m_id in filtered_displays:
                    break

                if isinstance(display, str) and m_id != display:
                    continue

                if not added:
                    filtered_displays[m_id] = monitor
                    added = True

                # if the display kwarg is an integer and we are currently at that index
                if isinstance(display, int) and len(filtered_displays) - 1 == display:
                    return [monitor]

                if added:
                    break
        return list(filtered_displays.values())

    duplicates = []
    for _ in range(3):
        duplicates = get_monitor_list()
        if duplicates:
            break
        time.sleep(0.4)
    else:
        msg = 'no displays detected'
        if method is not None:
            msg += f' with method: {method!r}'
        raise NoValidDisplayError(msg)

    monitors = filter_monitor_list(duplicates)
    if not monitors:
        # if no displays matched the query
        msg = 'no displays found'
        if display is not None:
            msg += f' with name/serial/model/edid/index of {display!r}'
        if method is not None:
            msg += f' with method of {method!r}'
        raise NoValidDisplayError(msg)

    return monitors


def __brightness(
    *args, display=None, method=None, meta_method='get', no_return=False,
    verbose_error=False, **kwargs
):
    '''Internal function used to get/set brightness'''
    logger.debug(
        f"brightness {meta_method} request display {display} with method {method}")

    def format_exc(name, e):
        errors.append((
            name, e.__class__.__name__,
            traceback.format_exc() if verbose_error else e
        ))

    output = []
    errors = []
    method = method.lower() if isinstance(method, str) else method

    for monitor in filter_monitors(display=display, method=method):
        try:
            if meta_method == 'set':
                monitor['method'].set_brightness(
                    *args, display=monitor['index'], **kwargs)
                if no_return:
                    output.append(None)
                    continue

            output += monitor['method'].get_brightness(
                display=monitor['index'], **kwargs)
        except Exception as e:
            output.append(None)
            format_exc(monitor, e)

    if output:
        output_is_none = set(output) == {None}
        if (
            # can't have None output if we are trying to get the brightness
            (meta_method == 'get' and not output_is_none)
            or (
                # if we are setting the brightness then we CAN have a None output
                # but only if no_return is True.
                meta_method == 'set'
                and ((no_return and output_is_none) or not output_is_none)
            )
        ):
            return None if no_return else output

    # if the function hasn't returned then it has failed
    msg = '\n'
    if errors:
        for monitor, exc_name, exc in errors:
            if isinstance(monitor, str):
                msg += f'\t{monitor}'
            else:
                msg += f'\t{monitor["name"]} ({monitor["serial"]})'
            msg += f' -> {exc_name}: '
            msg += str(exc).replace('\n', '\n\t\t') + '\n'
    else:
        msg += '\tno valid output was received from brightness methods'

    raise ScreenBrightnessError(msg)


if platform.system() == 'Windows':
    from . import windows
    _OS_MODULE = windows
    _OS_METHODS = (_OS_MODULE.WMI, _OS_MODULE.VCP)
elif platform.system() == 'Linux':
    from . import linux
    _OS_MODULE = linux
    _OS_METHODS = (
        _OS_MODULE.SysFiles, _OS_MODULE.I2C,
        _OS_MODULE.XRandr, _OS_MODULE.DDCUtil,
        _OS_MODULE.Light
    )
else:
    logger.warning(
        f'package imported on unsupported platform ({platform.system()})')
