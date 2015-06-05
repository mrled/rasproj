class statusmethod(object):
    '''
    Decorator for OnionPiImage methods which set their status level
    On the decorated function, set two attributes: 
     1) .status, which contains the status level that the function represents
     2) .description, which contains a description of that status for the end user
    Then, when the function is called, set an attribute on the *instance of the class* that the function belongs to
      - .status, which will contain the function's status attribute
    For example: 

    class OnionPiImage(object):
        def __init__(self):
            self.status = 0
        @statusmethod(4, 'Set the status to 4 for test purposes')
        def test_stat4(self):
            print("My class instance is set to status lvl 4 now")
    img = OnionPiImage()
    print(img.status)  # prints 0
    img.test_stat4()
    print(img.status)  # prints 4

    In other words, when the decorated method is called from an instance of the class, that instance's .status property is set, without having to explicitly set it in the body of the method
    '''
    # def __init__(self, status, description):
    #     self.status = status
    #     self.description = description
    # def __call__(self, func):
    #     def wrapper(class_instance, *args, **kwargs):
    #         class_instance.set_status(self.status)
    #         func(class_instance, *args, **kwargs)
    #     wrapper.status = self.status
    #     wrapper.description = self.description
    #     return wrapper
    def __init__(self, status, description):
        global finalstatus
        finalstatus += 1
        self.status = finalstatus
    def __call__(self, func):
        def wrapper(class_instance, *args, **kwargs):
            class_instance.set_status(self.status)
            func(class_instance, *args, **kwargs)
        wrapper.status = self.status
        return wrapper

    # statusmethods = {
    #     'test': __str__,
    #     0: lambda *a, **k: None,          # noop
    #     1: create_image,
    #     2: partition_image,
    #     3: create_image_filesystems,
    #     4: debootstrap_stage1,
    #     5: debootstrap_stage3,
    #     6: install_kernel,
    #     7: generate_checksum }
    # finalstatus = len(statusmethods) -1

    # statusmethods = [
    #     {lambda *a, **k: None,     'Image file has not yet been created'},
    #     {create_image,             'Image file has been created'},
    #     {partition_image,          'Image has been created and partitioned'},
    #     {create_image_filesystems, 'Filesystems have been created on the image partitions'},
    #     {debootstrap_stage1,       'debootstrap has completed stage1 and stage2'},
    #     {install_kernel,           'A kernel has been installed on the device'},
    #     {generate_checksum,        'The image is finished and a checksum file has been generated'}]

    # statusmethods = [
    #     {'func': lambda *a, **k: None,     'desc': 'Image file has not yet been created'},
    #     {'func': create_image,             'desc': 'Image file has been created'},
    #     {'func': partition_image,          'desc': 'Image has been created and partitioned'},
    #     {'func': create_image_filesystems, 'desc': 'Filesystems have been created on the image partitions'},
    #     {'func': debootstrap_stage1,       'desc': 'debootstrap has completed stage1 and stage2'},
    #     {'func': install_kernel,           'desc': 'A kernel has been installed on the device'},
    #     {'func': generate_checksum,        'desc': 'The image is finished and a checksum file has been generated'}]
    # finalstatus = len(statusmethods) -1

